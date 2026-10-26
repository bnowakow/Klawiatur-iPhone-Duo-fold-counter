Actual SQL backup is private and Git-ignored under docker-data/backup/postgres/.
Only verification output, size/hash metadata and mocked test logs are retained here.
A full restore succeeded in an isolated PostgreSQL 17 container; 36 tables restored.
No live database data was restored/modified and no host cron was installed.
