/*
declaration:
  version: 0.1
  description: "Authenticate user by login and password hash, and return user profile with authorities if valid"
  method: post
  namespace: auth_users
  returns: json
  allowlist:
    query:
      - field: login
        type: string
        description: "User login name"
      - field: password
        type: string
        description: "User password hash for authentication"
  response:
    fields:
      - field: login
        type: string
        description: "Login name of the authenticated user"
      - field: first_name
        type: string
        description: "First name of the user"
      - field: last_name
        type: string
        description: "Last name of the user"
      - field: id_code
        type: string
        description: "Unique identifier (ID code) of the user"
      - field: display_name
        type: string
        description: "Display name used in the system"
      - field: authorities
        type: array
        items:
          type: string
          enum: ['ROLE_ADMINISTRATOR', 'ROLE_SERVICE_MANAGER', 'ROLE_CUSTOMER_SUPPORT_AGENT', 'ROLE_CHATBOT_TRAINER', 'ROLE_ANALYST', 'ROLE_UNAUTHENTICATED']
        description: "List of authority roles assigned to the user"
*/
SELECT
    login,
    first_name,
    last_name,
    id_code,
    display_name,
    authority_name AS authorities
FROM auth_users.denormalized_user_data AS d_1
WHERE
    id_code = :login
    AND password_hash = :password
    AND ARRAY_LENGTH(authority_name, 1) > 0
    AND created = (
        SELECT MAX(d_2.created)
        FROM auth_users.denormalized_user_data AS d_2
        WHERE d_1.id_code = d_2.id_code
    )
LIMIT 1;
