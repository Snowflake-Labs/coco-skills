# SPRKPY1094

PyOdbc connection requires the installment of the following driver.

Message: PyOdbc connection requires the installment of the following driver: <driver_name>.

Category: Warning

## Description

This issue appears when the converted PyODBC connection requires a specific ODBC driver to be installed on the system. The SMA identifies the required driver based on the source JDBC connection type.

Common required drivers include:
- **ODBC Driver 18 for SQL Server** - For Microsoft SQL Server connections
- **Oracle in OraClient19Home1** - For Oracle database connections
- **MySQL ODBC Driver** - For MySQL connections
- **PostgreSQL ODBC Driver** - For PostgreSQL connections

## Resolution

This is an informational warning about driver requirements. **No code changes needed**, but ensure the specified ODBC driver is installed on the target system.

### Installation Steps:

**For SQL Server (ODBC Driver 18):**
- Download from: https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
- Install the appropriate version for your operating system

**For Oracle:**
- Install Oracle Instant Client with ODBC support
- Configure the TNS_ADMIN environment variable if using Oracle wallet

**For other databases:**
- Consult the database vendor's documentation for ODBC driver installation

## Additional recommendations

- Verify the driver is installed before running the converted code
- Ensure the driver version is compatible with your target environment
- Update connection strings if using a different driver version
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
