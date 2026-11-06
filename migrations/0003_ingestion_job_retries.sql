ALTER TABLE ingestion_jobs ADD COLUMN retry_of_job_id INTEGER REFERENCES ingestion_jobs(id) ON DELETE SET NULL;
ALTER TABLE ingestion_jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX ix_ingestion_jobs_retry_of_job_id ON ingestion_jobs(retry_of_job_id);
