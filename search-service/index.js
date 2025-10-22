const express = require("express");
const cors = require("cors");
const { Client } = require("@opensearch-project/opensearch");
const fs = require("fs");
const path = require("path");

const app = express();
app.use(cors());
app.use(express.json({ limit: "10mb" })); // Support large bulk requests

console.log("🚀 Starting Search Service...");

// Configuration
const config = {
  opensearch: {
    node: process.env.OPENSEARCH_URL || "http://opensearch-node:9200",
  },
  dataPath: process.env.DATA_PATH || "/app/data",
};

console.log("📋 Configuration:", config);

// Initialize OpenSearch client
const opensearch = new Client({
  node: config.opensearch.node,
  requestTimeout: 30000,
  pingTimeout: 10000,
});

// Test OpenSearch connection
async function testConnection() {
  try {
    const health = await opensearch.cluster.health();
    console.log("✅ OpenSearch connected:", health.body.status);
  } catch (error) {
    console.error("❌ OpenSearch connection failed:", error.message);
  }
}

// Create index for a source
async function createSourceIndex(sourceId) {
  const indexName = `source_${sourceId}`;

  try {
    const exists = await opensearch.indices.exists({ index: indexName });

    if (!exists.body) {
      const indexConfig = {
        index: indexName,
        body: {
          mappings: {
            properties: {
              source_file_id: { type: "keyword" },
              document_type: { type: "keyword" },
              page_title: { type: "text", analyzer: "standard" },
              file_name: { type: "text" },
              url: { type: "keyword" },
              subsector: { type: "keyword" },
              content: { type: "text", analyzer: "standard" },
              indexed_at: { type: "date" },
              file_size: { type: "integer" },
            },
          },
          settings: {
            number_of_shards: 1,
            number_of_replicas: 0,
          },
        },
      };

      await opensearch.indices.create(indexConfig);
      console.log(`✅ Created index: ${indexName}`);
    }

    return indexName;
  } catch (error) {
    console.error(`❌ Error creating index ${indexName}:`, error.message);
    throw error;
  }
}

// Read file content from volume
function readFileContent(filePath) {
  try {
    const fullPath = path.join(config.dataPath, filePath);

    if (!fs.existsSync(fullPath)) {
      console.warn(`⚠️  File not found: ${fullPath}`);
      return null;
    }

    const content = fs.readFileSync(fullPath, "utf8");
    return content;
  } catch (error) {
    console.error(`❌ Error reading file ${filePath}:`, error.message);
    return null;
  }
}

// Bulk index documents
app.post("/index/:sourceId/bulk", async (req, res) => {
  try {
    const { sourceId } = req.params;
    const { documents } = req.body;

    if (!documents || !Array.isArray(documents)) {
      return res.status(400).json({ error: "Documents array is required" });
    }

    console.log(
      `📦 Bulk indexing ${documents.length} documents for source: ${sourceId}`
    );

    // Create index if it doesn't exist
    const indexName = await createSourceIndex(sourceId);

    // Prepare bulk operations
    const bulkOperations = [];
    const results = {
      indexed: 0,
      failed: 0,
      errors: [],
    };

    for (const doc of documents) {
      try {
        // Read content from file
        const content = readFileContent(doc.content_path);

        if (!content) {
          results.failed++;
          results.errors.push(`Failed to read: ${doc.content_path}`);
          continue;
        }

        // Prepare document for indexing
        const document = {
          source_file_id: doc.source_file_id,
          document_type: doc.document_type,
          page_title: doc.page_title,
          file_name: doc.file_name,
          url: doc.url,
          subsector: doc.subsector,
          content: content,
          indexed_at: new Date().toISOString(),
          file_size: content.length,
        };

        // Add to bulk operations
        bulkOperations.push({
          index: {
            _index: indexName,
            _id: `${doc.source_file_id}_${doc.document_type}`,
          },
        });
        bulkOperations.push(document);
      } catch (error) {
        results.failed++;
        results.errors.push(
          `Error processing ${doc.source_file_id}: ${error.message}`
        );
      }
    }

    // Execute bulk operation
    if (bulkOperations.length > 0) {
      const bulkResponse = await opensearch.bulk({
        body: bulkOperations,
      });

      // Process results
      if (bulkResponse.body.errors) {
        bulkResponse.body.items.forEach((item, index) => {
          if (item.index?.error) {
            results.failed++;
            results.errors.push(`Bulk error: ${item.index.error.reason}`);
          } else {
            results.indexed++;
          }
        });
      } else {
        results.indexed = bulkOperations.length / 2; // Each doc has 2 operations
      }
    }

    console.log(
      `✅ Bulk indexing completed: ${results.indexed} indexed, ${results.failed} failed`
    );

    res.json({
      success: true,
      source_id: sourceId,
      results: results,
    });
  } catch (error) {
    console.error("❌ Bulk indexing error:", error.message);
    res
      .status(500)
      .json({ error: "Bulk indexing failed", details: error.message });
  }
});

// Search in source
app.get("/search/:sourceId", async (req, res) => {
  try {
    const { sourceId } = req.params;
    const { q = "", page = 1, size = 10, onlyIds = false } = req.query;

    const indexName = `source_${sourceId}`;

    // Check if index exists
    const exists = await opensearch.indices.exists({ index: indexName });
    if (!exists.body) {
      return res.json({
        results: [],
        sourceFileIds: [],
        total: 0,
        page: parseInt(page),
        size: parseInt(size),
        totalPages: 0,
      });
    }

    if (onlyIds === "true") {
      // For onlyIds, we need to paginate by unique source_file_ids

      // First, get all matching documents (no pagination yet)
      const allDocsQuery = {
        index: indexName,
        body: {
          size: 1000, // Get more docs to find unique source_file_ids
          query: {
            bool: {
              should: q.trim()
                ? [
                    {
                      term: {
                        url: {
                          value: q.trim(),
                          boost: 100,
                        },
                      },
                    },
                    {
                      multi_match: {
                        query: q.trim(),
                        fields: [
                          "url^5",
                          "content^3",
                          "page_title^2",
                          "file_name^2",
                        ],
                        type: "best_fields",
                      },
                    },
                  ]
                : [{ match_all: {} }],
              minimum_should_match: q.trim() ? 1 : 0,
            },
          },
          _source: ["source_file_id"],
          sort: ["_score"],
        },
      };

      const allDocsResponse = await opensearch.search(allDocsQuery);

      // Extract unique source_file_ids while preserving order
      const uniqueSourceFileIds = [];
      const seen = new Set();

      for (const hit of allDocsResponse.body.hits.hits) {
        const sourceFileId = hit._source.source_file_id;
        if (!seen.has(sourceFileId)) {
          seen.add(sourceFileId);
          uniqueSourceFileIds.push(sourceFileId);
        }
      }

      // Apply pagination to unique source_file_ids
      const totalUnique = uniqueSourceFileIds.length;
      const startIndex = (parseInt(page) - 1) * parseInt(size);
      const endIndex = startIndex + parseInt(size);
      const paginatedIds = uniqueSourceFileIds.slice(startIndex, endIndex);

      return res.json({
        sourceFileIds: paginatedIds,
        total: totalUnique,
        page: parseInt(page),
        size: parseInt(size),
        totalPages: Math.ceil(totalUnique / parseInt(size)),
      });
    } else {
      // For full results, return documents with pagination
      const searchQuery = {
        index: indexName,
        body: {
          from: (parseInt(page) - 1) * parseInt(size),
          size: parseInt(size),
          query: {
            bool: {
              should: q.trim()
                ? [
                    {
                      term: {
                        url: {
                          value: q.trim(),
                          boost: 100,
                        },
                      },
                    },
                    {
                      multi_match: {
                        query: q.trim(),
                        fields: [
                          "url^5",
                          "content^3",
                          "page_title^2",
                          "file_name^2",
                        ],
                        type: "best_fields",
                      },
                    },
                  ]
                : [{ match_all: {} }],
              minimum_should_match: q.trim() ? 1 : 0,
            },
          },
          highlight: {
            fields: {
              content: {
                fragment_size: 150,
                number_of_fragments: 3,
              },
            },
          },
          sort: ["_score"],
        },
      };

      const response = await opensearch.search(searchQuery);

      const results = response.body.hits.hits.map((hit) => ({
        id: hit._id,
        score: hit._score,
        source_file_id: hit._source.source_file_id,
        document_type: hit._source.document_type,
        page_title: hit._source.page_title,
        file_name: hit._source.file_name,
        url: hit._source.url,
        subsector: hit._source.subsector,
        indexed_at: hit._source.indexed_at,
        file_size: hit._source.file_size,
        content_preview: hit._source.content.substring(0, 200) + "...",
        highlights: hit.highlight || {},
      }));

      return res.json({
        results,
        total: response.body.hits.total.value,
        page: parseInt(page),
        size: parseInt(size),
        totalPages: Math.ceil(response.body.hits.total.value / parseInt(size)),
        query: q,
      });
    }
  } catch (error) {
    console.error("❌ Search error:", error.message);
    res.status(500).json({ error: "Search failed", details: error.message });
  }
});

// Get full document content
app.get("/document/:sourceId/:documentId", async (req, res) => {
  try {
    const { sourceId, documentId } = req.params;
    const indexName = `source_${sourceId}`;

    const response = await opensearch.get({
      index: indexName,
      id: documentId,
    });

    res.json({
      id: response.body._id,
      source: response.body._source,
    });
  } catch (error) {
    if (error.body?.found === false) {
      return res.status(404).json({ error: "Document not found" });
    }

    console.error("❌ Get document error:", error.message);
    res.status(500).json({ error: "Failed to retrieve document" });
  }
});

// Delete source index
app.delete("/index/:sourceId", async (req, res) => {
  try {
    const { sourceId } = req.params;
    const indexName = `source_${sourceId}`;

    const exists = await opensearch.indices.exists({ index: indexName });

    if (exists.body) {
      await opensearch.indices.delete({ index: indexName });
      console.log(`✅ Deleted index: ${indexName}`);
    }

    res.json({ success: true, message: `Index ${indexName} deleted` });
  } catch (error) {
    console.error(`❌ Error deleting index:`, error.message);
    res.status(500).json({ error: "Failed to delete index" });
  }
});

// Delete documents by source_file_id
app.delete("/documents/:sourceId/:sourceFileId", async (req, res) => {
  try {
    const { sourceId, sourceFileId } = req.params;
    const indexName = `source_${sourceId}`;

    // Check if index exists
    const exists = await opensearch.indices.exists({ index: indexName });
    if (!exists.body) {
      return res.status(404).json({
        error: "Index not found",
        source_id: sourceId,
      });
    }

    console.log(
      `🗑️  Deleting documents with source_file_id: ${sourceFileId} from source: ${sourceId}`
    );

    // Use delete by query to remove all documents with the specified source_file_id
    const deleteResponse = await opensearch.deleteByQuery({
      index: indexName,
      body: {
        query: {
          term: {
            source_file_id: sourceFileId,
          },
        },
      },
    });

    const deletedCount = deleteResponse.body.deleted;

    console.log(
      `✅ Deleted ${deletedCount} documents with source_file_id: ${sourceFileId}`
    );

    res.json({
      success: true,
      source_id: sourceId,
      source_file_id: sourceFileId,
      deleted_count: deletedCount,
      took: deleteResponse.body.took,
    });
  } catch (error) {
    console.error("❌ Delete by source_file_id error:", error.message);
    res.status(500).json({
      error: "Failed to delete documents",
      details: error.message,
    });
  }
});

// Health check
app.get("/health", async (req, res) => {
  try {
    const health = await opensearch.cluster.health();
    res.json({
      status: "healthy",
      opensearch: health.body.status,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    res.status(500).json({
      status: "unhealthy",
      error: error.message,
      timestamp: new Date().toISOString(),
    });
  }
});

// Get source statistics
app.get("/stats/:sourceId", async (req, res) => {
  try {
    const { sourceId } = req.params;
    const indexName = `source_${sourceId}`;

    const exists = await opensearch.indices.exists({ index: indexName });
    if (!exists.body) {
      return res.json({
        total_documents: 0,
        document_types: {},
        subsectors: {},
      });
    }

    const response = await opensearch.search({
      index: indexName,
      body: {
        size: 0,
        aggs: {
          document_types: {
            terms: { field: "document_type" },
          },
          subsectors: {
            terms: { field: "subsector" },
          },
        },
      },
    });

    res.json({
      total_documents: response.body.hits.total.value,
      document_types: response.body.aggregations.document_types.buckets,
      subsectors: response.body.aggregations.subsectors.buckets,
    });
  } catch (error) {
    console.error("❌ Stats error:", error.message);
    res.status(500).json({ error: "Failed to get statistics" });
  }
});

// Initialize and start server
async function start() {
  try {
    await testConnection();

    const PORT = process.env.PORT || 3000;
    app.listen(PORT, () => {
      console.log(`🔍 Search Service running on port ${PORT}`);
      console.log(`📁 Data path: ${config.dataPath}`);
    });
  } catch (error) {
    console.error("❌ Failed to start service:", error);
    process.exit(1);
  }
}

start();
