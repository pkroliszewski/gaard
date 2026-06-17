from typing import Literal

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False


class ForeignKeyInfo(BaseModel):
    constrained_columns: list[str] = Field(default_factory=list)
    referred_table: str
    referred_columns: list[str] = Field(default_factory=list)


class TableInfo(BaseModel):
    name: str
    object_type: Literal["table", "view"] = "table"
    columns: list[ColumnInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)


class DatabaseSchema(BaseModel):
    tables: list[TableInfo] = Field(default_factory=list)
