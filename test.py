import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO)

URI = "neo4j+s://297cd785.databases.neo4j.io"
AUTH = ("neo4j", "wCcW3W6hyC0EJNmyAf4bcwFrtRsPLGVmbG_c6eGHi9o")

driver = GraphDatabase.driver(URI, auth=AUTH)
with driver.session(database="neo4j") as session:
    print(session.run("RETURN 1 AS ok").single()["ok"])
driver.close()