"""Seed public-safe Phase 7 rows for the PostgreSQL 0016-to-0017 upgrade test."""
from secure_eval_wrapper.paper.demo import run_internal_demo
from secure_eval_wrapper.paper.persistence import PostgresPaperRepository
from secure_eval_wrapper.storage.postgres.config import load_postgres_config

def main():
    import psycopg
    connection=psycopg.connect(**load_postgres_config().to_connection_kwargs())
    try:
        result=run_internal_demo(persist_repository=PostgresPaperRepository(connection))
        print("OK: seeded public-safe Phase 7 rows run="+result["paper_run_id"])
    finally:connection.close()
if __name__=="__main__":main()
