-- No CREATE DATABASE here: the image already creates POSTGRES_DB before it
-- runs this file. Creating it a second time raised "database already exists",
-- and the entrypoint runs these scripts under ON_ERROR_STOP=1, so postgres
-- aborted mid-init on every fresh volume. `restart: unless-stopped` then
-- brought it back on a crash-recovered data directory, which hid the failure
-- locally and made `up --wait` fail at random in CI depending on whether it
-- polled inside the crash window - while the GRANT below never ran at all.
CREATE USER reader WITH PASSWORD 'password';

-- current_database() rather than a literal, so this keeps working if
-- POSTGRES_DB is changed in config/preflight.env.
DO $$ BEGIN
  EXECUTE format('GRANT ALL PRIVILEGES ON DATABASE %I TO reader', current_database());
END $$;
