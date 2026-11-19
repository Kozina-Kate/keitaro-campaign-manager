from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def build_database(url: str):
    options = {"check_same_thread": False} if url.startswith("sqlite:") else {}
    engine = create_engine(url, connect_args=options)
    return engine, sessionmaker(engine, expire_on_commit=False)
