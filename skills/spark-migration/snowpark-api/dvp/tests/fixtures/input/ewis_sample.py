"""Sample file with EWI comments for testing dvp-ewi-fixer skill."""

from snowflake.snowpark import Session

#EWI: SPRKPY1000 => Not supported spark version
def process_data(session: Session):
    df = session.table("RAW_DATA")
    
    #EWI: SPRKPY1001 => Unsupported DataFrame operation
    result = df.rdd.map(lambda x: x)  # needs Snowpark equivalent
    
    return result


#EWI: SPRKPY1000 => Not supported spark version
def another_function():
    pass
