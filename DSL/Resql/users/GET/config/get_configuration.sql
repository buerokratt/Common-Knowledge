/*
declaration:
  version: 0.1
  description: "Fetch the latest non-deleted configuration value for a specified key"
  method: get
  namespace: config
  returns: json
  allowlist:
    query:
      - field: key
        type: string
        description: "Configuration key to retrieve the latest value for"
  response:
    fields:
      - field: id
        type: string
        description: "Unique identifier of the configuration entry"
      - field: key
        type: string
        description: "Key of the configuration setting"
      - field: value
        type: string
        description: "Stored value associated with the key"
      - field: created
        type: timestamp
        description: "Timestamp when the configuration entry was created"
*/
SELECT
    id,
    key,
    value,
    created
FROM config.configuration AS c_1
WHERE
    key = :key
    AND created = (
        SELECT MAX(created) FROM config.configuration AS c_2
        WHERE c_1.key = c_2.key
    )
    AND NOT deleted;
