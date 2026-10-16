const fs = require('fs');
const path = require('path');

function loadConfig(env) {
  const endpoint = new URL(env.OPENSEARCH_URL);
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== '/') {
    throw new Error('Invalid OpenSearch origin');
  }
  if (endpoint.protocol === 'http:' && env.OPENSEARCH_ALLOW_HTTP !== 'true') {
    throw new Error('HTTP requires explicit opt-in');
  }
  const indexPrefix = env.OPENSEARCH_INDEX_PREFIX;
  if (!indexPrefix || !/^[a-z][a-z0-9-]{0,60}-$/.test(indexPrefix)) throw new Error('Invalid index prefix');
  const username = fs.readFileSync(env.OPENSEARCH_USERNAME_FILE, 'utf8');
  const password = fs.readFileSync(env.OPENSEARCH_PASSWORD_FILE, 'utf8');
  if (!username || !password || /[\r\n:]/.test(username) || /[\r\n]/.test(password)) throw new Error('Invalid credentials');
  const ssl = { rejectUnauthorized: true };
  if (env.OPENSEARCH_CA_FILE) ssl.ca = fs.readFileSync(env.OPENSEARCH_CA_FILE, 'utf8');
  return {
    client: { node: endpoint.origin, auth: { username, password }, ssl, maxRetries: 0, requestTimeout: 30000, pingTimeout: 10000 },
    indexPrefix,
    dataPath: env.DATA_PATH || '/app/data',
  };
}

function sourceIndex(config, sourceId) {
  if (typeof sourceId !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,127}$/.test(sourceId)) throw new Error('Invalid source ID');
  return `${config.indexPrefix}source_${sourceId}`;
}

function readContent(root, file) {
  if (typeof file !== 'string' || !file || path.isAbsolute(file)) throw new Error('Invalid content path');
  const base = fs.realpathSync(root);
  const candidate = fs.realpathSync(path.resolve(base, file));
  if (!candidate.startsWith(base + path.sep) || !fs.statSync(candidate).isFile()) throw new Error('Content outside data volume');
  return fs.readFileSync(candidate, 'utf8');
}

function bulkResults(body, expected) {
  // Never count a missing/malformed receipt as an accepted document.
  if (!body || !Array.isArray(body.items) || body.items.length !== expected) throw new Error('Invalid bulk receipt');
  const result = { indexed: 0, failed: 0, errors: [] };
  for (const item of body.items) {
    const receipt = item && item.index;
    if (receipt && Number.isInteger(receipt.status) && receipt.status >= 200 && receipt.status < 300 && !receipt.error) result.indexed++;
    else { result.failed++; result.errors.push('Bulk item failed'); }
  }
  return result;
}

module.exports = { loadConfig, sourceIndex, readContent, bulkResults };
