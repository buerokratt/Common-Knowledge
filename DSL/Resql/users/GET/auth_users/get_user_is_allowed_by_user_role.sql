/*
declaration:
  version: 0.1
  description: "Check if the user has at least one role that matches the allowed roles"
  method: get
  namespace: auth_users
  returns: json
  allowlist:
    query:
      - field: userId
        type: string
        description: "Unique identifier of the user to check roles for"
      - field: allowedRoles
        type: string
        description: "Comma-separated list of allowed role names to check against the user's authorities"
  response:
    fields:
      - field: is_allowed
        type: boolean
        description: "True if the user has at least one allowed role; otherwise false"
*/
SELECT COALESCE(
    (
        SELECT ARRAY_AGG(UPPER(authorities::TEXT))::TEXT [] AS user_roles
        FROM (
            SELECT UNNEST(authority_name) AS authorities
            FROM auth_users.denormalized_user_data
            WHERE id_code = :userId::TEXT
            ORDER BY UNNEST(authority_name)::TEXT ASC
        ) AS _
    ) && (
        SELECT(
            SELECT ARRAY_AGG(UPPER(allowed_role))::TEXT [] AS roles
            FROM (
                SELECT
                    UNNEST(STRING_TO_ARRAY(:allowedRoles, ',')::TEXT []) AS allowed_role
                ORDER BY allowed_role ASC
            ) AS __
        )
    ),
    FALSE
) AS is_allowed;
