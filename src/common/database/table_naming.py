from sqlmodel import SQLModel

# Predictable names for every index and constraint. Migrations can only drop or
# change a constraint they can name, and SQLite gives none by default.
TABLE_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Set on the shared metadata before any table class is defined; every module
# that defines tables imports this first.
SQLModel.metadata.naming_convention = TABLE_NAMING_CONVENTION
