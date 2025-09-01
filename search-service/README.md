# Search Service

A Node.js service that provides full-text search capabilities for the Common Knowledge Base using OpenSearch. The service indexes cleaned content and provides fast, relevant search results across all collected documents.

## Overview

The search service is responsible for:
- Indexing cleaned content from all data sources
- Providing full-text search across indexed documents
- Managing search indices per data source
- Supporting bulk document operations
- Generating search statistics and analytics

## Architecture

```
search-service/
├── index.js             # Main application and API endpoints
├── package.json         # Node.js dependencies and scripts
└── Dockerfile          # Container configuration
```

## Features

### Search Capabilities

1. **Full-text Search**: Multi-field search across content, titles, and file names
2. **Source-based Indexing**: Separate indices for each data source
3. **Relevance Scoring**: Content-weighted scoring for better results
4. **Pagination**: Efficient pagination for large result sets
5. **Highlighting**: Search term highlighting in results

### Index Management

1. **Automatic Index Creation**: Creates indices on-demand for new sources
2. **Bulk Indexing**: Efficient bulk document insertion
3. **Document Deletion**: Remove documents by source file ID
4. **Index Statistics**: Document counts and aggregations

## API Endpoints

### POST /index/{sourceId}/bulk
Bulk index documents for a specific source.

**Request Body:**
```json
{
  "documents": [
    {
      "source_file_id": "uuid",
      "document_type": "cleaned",
      "page_title": "Document Title",
      "file_name": "document.txt",
      "url": "https://source-url.com",
      "subsector": "sector-name",
      "content_path": "relative/path/to/cleaned.txt"
    }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "source_id": "source123",
  "results": {
    "indexed": 10,
    "failed": 0,
    "errors": []
  }
}
```

### GET /search/{sourceId}
Search documents within a specific source.

**Query Parameters:**
- `q`: Search query string
- `page`: Page number (default: 1)
- `size`: Results per page (default: 10)  
- `onlyIds`: Return only source file IDs (true/false)

**Response (Full Results):**
```json
{
  "results": [
    {
      "id": "doc_id",
      "score": 1.25,
      "source_file_id": "uuid",
      "document_type": "cleaned",
      "page_title": "Document Title",
      "file_name": "document.txt",
      "url": "https://source-url.com",
      "subsector": "sector-name",
      "indexed_at": "2025-01-01T12:00:00Z",
      "file_size": 1024,
      "content_preview": "Document content preview...",
      "highlights": {
        "content": ["highlighted <em>text</em> snippets"]
      }
    }
  ],
  "total": 100,
  "page": 1,
  "size": 10,
  "totalPages": 10,
  "query": "search terms"
}
```

**Response (IDs Only):**
```json
{
  "sourceFileIds": ["uuid1", "uuid2", "uuid3"],
  "total": 50,
  "page": 1,
  "size": 10,
  "totalPages": 5
}
```

### GET /document/{sourceId}/{documentId}
Retrieve full document content by ID.

**Response:**
```json
{
  "id": "document_id",
  "source": {
    "source_file_id": "uuid",
    "content": "Full document content...",
    "page_title": "Document Title",
    "file_name": "document.txt",
    "url": "https://source-url.com",
    "indexed_at": "2025-01-01T12:00:00Z"
  }
}
```

### DELETE /index/{sourceId}
Delete entire index for a source.

**Response:**
```json
{
  "success": true,
  "message": "Index source_123 deleted"
}
```

### DELETE /documents/{sourceId}/{sourceFileId}
Delete all documents for a specific source file.

**Response:**
```json
{
  "success": true,
  "source_id": "source123",
  "source_file_id": "uuid",
  "deleted_count": 3,
  "took": 15
}
```

### GET /stats/{sourceId}
Get statistics for a source index.

**Response:**
```json
{
  "total_documents": 1500,
  "document_types": [
    {"key": "cleaned", "doc_count": 1200},
    {"key": "raw", "doc_count": 300}
  ],
  "subsectors": [
    {"key": "healthcare", "doc_count": 800},
    {"key": "education", "doc_count": 700}
  ]
}
```

### GET /health
Service health check.

**Response:**
```json
{
  "status": "healthy",
  "opensearch": "green",
  "timestamp": "2025-01-01T12:00:00Z"
}
```

## Environment Variables

- `OPENSEARCH_URL`: OpenSearch cluster URL (default: http://opensearch-node:9200)
- `DATA_PATH`: Path to mounted data volume (default: /app/data)
- `PORT`: Service port (default: 3000)

## Dependencies

- **@opensearch-project/opensearch 2.5.0**: OpenSearch client library
- **express 4.18.2**: Web framework for API endpoints
- **cors 2.8.5**: Cross-origin resource sharing support

## Index Schema

### Document Mapping

```json
{
  "mappings": {
    "properties": {
      "source_file_id": {"type": "keyword"},
      "document_type": {"type": "keyword"},
      "page_title": {"type": "text", "analyzer": "standard"},
      "file_name": {"type": "text"},
      "url": {"type": "keyword"},
      "subsector": {"type": "keyword"},
      "content": {"type": "text", "analyzer": "standard"},
      "indexed_at": {"type": "date"},
      "file_size": {"type": "integer"}
    }
  }
}
```

### Index Settings

- **Shards**: 1 (suitable for moderate data volumes)
- **Replicas**: 0 (adjust based on availability requirements)
- **Analyzer**: Standard analyzer for text processing

## Running the Service

### Development

```bash
# Install dependencies
npm install

# Start development server with auto-reload
npm run dev

# Start production server
npm start
```

### Docker

```bash
# Build the image
docker build -t search-service .

# Run the container
docker run -p 3000:3000 \
  -e OPENSEARCH_URL="http://opensearch:9200" \
  -v /data:/app/data \
  search-service
```

## Integration

The search service integrates with:
- **OpenSearch Cluster**: Primary search engine backend
- **File Processing**: Reads cleaned content files from mounted volumes
- **GUI**: Provides search functionality for web interface
- **External API**: Search endpoints accessible via Ruuter

## Performance Considerations

### Indexing Performance

1. **Bulk Operations**: Use bulk indexing for multiple documents
2. **File Reading**: Efficient file system access for content reading
3. **Memory Management**: 10MB request limit for large bulk operations
4. **Connection Pooling**: Persistent OpenSearch connections

### Search Performance

1. **Field Weighting**: Content (3x), page title (2x), file name (2x)
2. **Result Limiting**: Configurable page sizes for response management
3. **Highlighting**: Fragment-based content highlighting
4. **Caching**: Consider adding Redis caching for frequent queries

## Data Volume Management

### Index Strategy

- **Per-Source Indices**: Separate index for each data source
- **Document ID Format**: `{source_file_id}_{document_type}`
- **Index Naming**: `source_{source_id}`

### Storage Management

- **Content Files**: Read from mounted data volumes
- **Index Storage**: OpenSearch manages index storage
- **Cleanup**: Automatic document deletion when files are removed

## Monitoring

### Health Monitoring

- **Service Health**: `/health` endpoint for service status
- **OpenSearch Health**: Cluster status monitoring
- **Index Statistics**: Document counts and storage metrics

### Error Handling

- **Connection Errors**: Graceful handling of OpenSearch unavailability
- **File Errors**: Robust file reading with error logging
- **Index Errors**: Proper error responses for index operations

## Development

### Adding New Features

1. **New Endpoints**: Add routes to index.js with proper error handling
2. **Index Changes**: Update mapping configuration for new fields
3. **Search Features**: Extend search query building and result formatting
4. **Testing**: Test with sample data and various query patterns

### Configuration Updates

- **Mapping Changes**: Requires index recreation or mapping updates
- **Settings Changes**: Can be updated with index settings API
- **Performance Tuning**: Adjust shard/replica settings based on usage

## Troubleshooting

### Common Issues

1. **OpenSearch Connection**
   - Verify OpenSearch cluster is running and accessible
   - Check network connectivity and firewall rules
   - Review OpenSearch logs for errors

2. **File Reading Errors**
   - Ensure data volume is properly mounted
   - Verify file permissions and paths
   - Check disk space availability

3. **Indexing Failures**
   - Review document format and required fields
   - Check OpenSearch cluster health and storage
   - Monitor memory usage during bulk operations

### Debug Commands

```bash
# Check service health
curl http://localhost:3000/health

# Test search functionality
curl "http://localhost:3000/search/test_source?q=search+term"

# Get source statistics
curl http://localhost:3000/stats/source_id

# Check OpenSearch cluster status
curl http://opensearch:9200/_cluster/health
```