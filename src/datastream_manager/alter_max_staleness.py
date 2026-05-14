"""
Used to alter the max staleness of tables in BigQuery
Specifically, when Datastream is used to replicate data to BigQuery,
a default max staleness (configured in Datastream) is set. But this
is a property of the table, not the stream. This script allows it
to be altered cleanly and easily.
Author: Padraig Alton, 2025
Note:
  Open-source thanks to the kind permission of JustPark Parking Ltd,
  my employer at the time I wrote this utility script
"""

import logging
import os

from pathlib import Path

from cyclopts import App
from google.cloud.bigquery import Client


def setup_logger(logger_name: str) -> logging.Logger:
    """
    Set up and configure a logger with both stream and file handlers
    Inputs:
        logger_name - the name of the logger
    Outputs:
        logger - a configured logger instance
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    # Create handlers
    stream_handler = logging.StreamHandler()
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(log_dir / f"{logger_name}.log")

    # Set levels
    stream_handler.setLevel(logging.INFO)
    file_handler.setLevel(logging.INFO)

    # Create formatters and add it to handlers
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

    return logger


def _validate_identifiers(*identifiers: str):
    """
    Validate identifiers to prevent breaking out of backtick-quoted names
    Inputs:
        *identifiers - pass as many identifiers for validation as needed
    Raises:
        ValueError - if any identifier contains backticks
    """
    for _id in identifiers:
        if "`" in _id:
            emsg = f"Identifier '{_id}' cannot contain backticks"
            raise ValueError(emsg)


def get_tables_with_staleness_guarantee(
    client: Client, project: str, dataset: str
) -> list[str]:
    """
    Get all tables in a dataset with the max staleness guarantee set
    Inputs:
        client  - the BigQuery client
        project - the project name
        dataset - the dataset name
    Outputs:
        tables - a list of table names with a max staleness guarantee set
    """
    sql_query = f"""
    SELECT
        table_name
    FROM
        `{project}`.`{dataset}`.INFORMATION_SCHEMA.TABLE_OPTIONS
    WHERE
        option_name = 'max_staleness'
    ORDER BY
        table_name
    """
    tables = [rec[0] for rec in client.query(sql_query).result()]
    return tables


def alter_max_staleness(
    client: Client,
    project: str,
    dataset: str,
    table_name: str,
    ddl_interval: str,
) -> None:
    """
    Alter the max staleness of a source table in BigQuery
    Inputs:
        client - the BigQuery client
        project - the project name
        dataset - the dataset name
        table_name - the table name
        ddl_interval - the max staleness to set (e.g. INTERVAL '2' HOUR)
    Raises:
        google.api_core.exceptions.NotFound - if the target table is not found
    """
    ddl_update = f"""
        ALTER TABLE `{project}`.`{dataset}`.`{table_name}`
        SET OPTIONS (
            max_staleness = {ddl_interval}
        );
    """
    result = client.query(ddl_update)
    _ = result.result()  # will raise an exception if anything went wrong


def parse_interval(max_staleness: str) -> str:
    """
    Parse the max staleness string into a DDL interval string
    Inputs:
        max_staleness - the max staleness to set (e.g. '2h', '15m', etc.)
    Outputs:
        ddl_interval - the DDL interval string (e.g. INTERVAL '2' HOUR)
    """
    unit_lookup = {
        "h": "HOUR",
        "m": "MINUTE",
        "s": "SECOND",
    }
    # for validation, casting to int and doing a strict
    #  unit lookup will be sufficient
    interval_size = int(max_staleness[:-1])
    interval_unit = unit_lookup[max_staleness[-1]]
    ddl_interval = f"INTERVAL '{interval_size}' {interval_unit}"
    return ddl_interval


HELP_MSG = """
Alter the max staleness of a source table in BigQuery
(useful for Datastream-replicated tables)
"""

USAGE_MSG = """
alter_max_staleness.py [ARGS] [OPTIONS]
Examples:
python alter_max_staleness.py source_dataset table_name 2h
python alter_max_staleness.py source_dataset ALL_TABLES 2h
"""


app = App(
    help=HELP_MSG,
    usage=USAGE_MSG,
)


@app.default
def main(
    dataset: str,
    table_name: str,
    max_staleness: str = "1h",
    project: str | None = None,
):
    """
    Alter the maximum staleness option of a BigQuery table,
    or all tables in the target dataset with max_staleness
    already set

    Inputs:
        dataset - the dataset name
        table_name - the table name (or special value "ALL_TABLES")
        max_staleness - the max staleness to set (e.g. '2h', '15m', etc.)
        project - GCP project name
            (will attempt to read $GOOGLE_CLOUD_PROJECT if not set)

    """
    logger = setup_logger("alter_max_staleness")
    client = Client()

    ddl_interval = parse_interval(max_staleness)
    if project is None:
        project = os.environ["GOOGLE_CLOUD_PROJECT"]

    _validate_identifiers(project, dataset, table_name)

    logger.info(
        "Starting max staleness update: dataset=%s, table_name=%s, max_staleness=%s",
        dataset,
        table_name,
        max_staleness,
    )

    if table_name != "ALL_TABLES":
        logger.info(
            "Altering max staleness for table: %s.%s.%s", project, dataset, table_name
        )
        alter_max_staleness(client, project, dataset, table_name, ddl_interval)
        logger.info(
            "Successfully updated max staleness for table: %s.%s.%s",
            project,
            dataset,
            table_name,
        )
    # alter max staleness for all tables in the dataset with max_staleness already set
    else:
        logger.info(
            "Finding all tables in dataset %s with max_staleness option set", dataset
        )
        tables = get_tables_with_staleness_guarantee(client, project, dataset)
        logger.info("Found %d table(s) with max_staleness option set", len(tables))
        for table in tables:
            logger.info(
                "Altering max staleness for table: %s.%s.%s", project, dataset, table
            )
            alter_max_staleness(client, project, dataset, table, ddl_interval)
            logger.info(
                "Successfully updated max staleness for table: %s.%s.%s",
                project,
                dataset,
                table,
            )
        logger.info("Successfully updated max staleness for %d table(s)", len(tables))


if __name__ == "__main__":
    app()
