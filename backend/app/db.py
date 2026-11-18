from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, Integer, String, Text, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/app.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
if DATABASE_URL.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
                       pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Dataset(Base):
    __tablename__ = "datasets"
    name = Column(String(64), primary_key=True)
    content = Column(Text, nullable=False)
    rows = Column(Integer, default=0)
    uploaded_by = Column(String(120))
    uploaded_at = Column(DateTime, default=now)


class ContractRow(Base):
    __tablename__ = "contracts"
    contract_id = Column(String(40), primary_key=True)
    supplier_id = Column(String(20))
    supplier_name = Column(String(200))
    start_date = Column(String(10))
    end_date = Column(String(10))
    value_cap = Column(String(40))
    consumed_value = Column(String(40), default="0")
    source = Column(String(20), default="register")  # register | pdf | manual
    lines = Column(JSON, default=list)  # [{item_code, description, uom, rate}]
    pdf_name = Column(String(200))
    added_by = Column(String(120))
    added_at = Column(DateTime, default=now)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(20), nullable=False)
    effective_date = Column(String(10), nullable=False)
    status = Column(String(20), default="draft")  # draft | active | rejected
    body = Column(JSON, nullable=False)
    changes = Column(JSON, default=list)  # plain-language change list
    source_type = Column(String(20))  # seed | document | chat | editor
    source_text = Column(Text)
    based_on = Column(Integer)
    created_by = Column(String(120))
    created_at = Column(DateTime, default=now)
    approved_by = Column(String(120))
    approved_at = Column(DateTime)
    review_note = Column(Text)


class PRLineRow(Base):
    __tablename__ = "pr_lines"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pr_no = Column(String(40))
    pr_line = Column(String(10), default="1")
    data = Column(JSON, nullable=False)
    source = Column(String(20), default="chat")
    status = Column(String(20), default="open")
    created_by = Column(String(120))
    created_at = Column(DateTime, default=now)


class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(String(200))
    source = Column(String(20))  # csv | chat | api
    policy_mode = Column(String(60))  # "effective-dated" or "forced v5.0"
    line_count = Column(Integer)
    input_lines = Column(JSON)
    result = Column(JSON)
    created_by = Column(String(120))
    created_at = Column(DateTime, default=now)


class BuyerAction(Base):
    __tablename__ = "buyer_actions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer)
    group_id = Column(String(20))
    action = Column(String(20))  # approve | override
    document_no = Column(String(40))
    comment = Column(Text)
    original = Column(JSON)
    final = Column(JSON)
    actor = Column(String(120))
    at = Column(DateTime, default=now)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(String(40), primary_key=True)
    state = Column(JSON, default=dict)
    history = Column(JSON, default=list)
    user = Column(String(120))
    updated_at = Column(DateTime, default=now)


class Audit(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(DateTime, default=now)
    actor = Column(String(120))
    action = Column(String(60))
    entity = Column(String(120))
    details = Column(JSON)


def init_db():
    Base.metadata.create_all(engine)
