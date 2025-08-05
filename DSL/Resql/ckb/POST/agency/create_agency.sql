INSERT INTO agency (name, sector, external_id)
VALUES (:name, :sector, :external_id)
RETURNING id, base_id, name, sector, external_id, created_at, updated_at;
