/*
declaration:
  version: 0.1
  description: "Authenticate user by login and password, returning profile data if user has authorities"
  method: get
  namespace: auth_users
  returns: json
  allowlist:
    query:
      - field: login
        type: string
        description: "User login name"
  response:
    fields:
      - field: login
        type: string
        description: "User's login"
      - field: first_name
        type: string
        description: "User's first name"
      - field: last_name
        type: string
        description: "User's last name"
      - field: id_code
        type: string
        description: "Unique identifier for the user"
      - field: display_name
        type: string
        description: "Full display name"
      - field: authorities
        type: array
        items:
          type: string
          enum: ['ROLE_ADMINISTRATOR', 'ROLE_SERVICE_MANAGER', 'ROLE_CUSTOMER_SUPPORT_AGENT', 'ROLE_CHATBOT_TRAINER', 'ROLE_ANALYST', 'ROLE_UNAUTHENTICATED']
        description: "List of user authorities"
*/
SELECT DISTINCT u.login,
       u.first_name,
       u.last_name,
       u.id_code,
       u.display_name,
       u.csa_title,
       u.csa_email,
       ua.authority_name AS authorities
FROM "user" u
         LEFT JOIN (SELECT authority_name, user_id
                     FROM user_authority AS ua
                     WHERE ua.id IN (SELECT max(id)
                                     FROM user_authority
                                     GROUP BY user_id)) ua ON u.id_code = ua.user_id
WHERE login = :login;