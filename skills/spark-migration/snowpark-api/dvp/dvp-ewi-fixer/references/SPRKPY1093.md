# SPRKPY1093

Jdbc connection option is not supported. Try adding it manually to the pyodbc connection placeholders.

Message: Jdbc connection option "<option_name>" is not supported. Try adding it manually to the pyodbc connection placeholders.

Category: Warning

## Description

This issue appears when a JDBC connection option from PySpark is flagged during SMA conversion to PyODBC. **In most cases, SMA already correctly maps these options automatically.** This warning is informational to alert you that certain JDBC-specific options were present in the original code.

Common options and their mappings:
- `url` - JDBC connection URL → **Not needed** (PyODBC uses DRIVER/SERVER/DATABASE format instead)
- `user` - Username for authentication → **Already mapped to `UID`**
- `password` - Password for authentication → **Already mapped to `PWD`**
- `walletUri` - Oracle wallet URI → **Cannot be auto-mapped**, requires manual configuration
- `connectionId` - Connection identifier → **Cannot be auto-mapped**, requires manual configuration

## Resolution

### Step 1: Verify Auto-Mapped Options

Check that SMA already mapped the standard JDBC options:

```python
# SMA converts this automatically:
# JDBC: properties={"user": "username", "password": "password"}
# PyODBC: UID={username}; PWD={password};
```

**✅ If you see `UID` and `PWD` in your PyODBC connection string, the mapping is already complete.** Simply remove the EWI warning comments.

### Step 2: Handle Special Options Manually

For options that cannot be auto-mapped (walletUri, connectionId, etc.):

**Example: Oracle Wallet**
```python
# Original JDBC with walletUri
df = spark.read.jdbc(
    url="jdbc:oracle:thin:@...",
    table="table_name",
    properties={
        "walletUri": "oci://<bucket>@<namespace>/Wallet_DB.zip",
        "user": "username",
        "password": "password"
    }
)

# Converted PyODBC - add wallet configuration manually
import os
os.environ['TNS_ADMIN'] = '/path/to/wallet'

df = spark.read.dbapi(
    pyodbc.connect(
        f"DRIVER={{Oracle in OraClient19Home1}};"
        f"DBQ={tns_alias};"
        f"UID={username};"
        f"PWD={password};"
    ),
    table="table_name"
)
```

## Additional recommendations

- **Most cases**: Just remove the EWI warning - SMA already did the conversion
- **Special cases**: Manually configure walletUri, connectionId, or other database-specific options
- Review the PyODBC connection string and ensure all necessary connection parameters are included
- Consult PyODBC documentation for driver-specific connection string formats
- Test the connection after reviewing to ensure it works correctly
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
