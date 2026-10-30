"""The Sentinel data + ML pipeline as an Apache Airflow DAG.

This file is NOT executed by the project (Airflow is not a dependency). It mirrors
`sentinel/platform/pipeline.py::data_platform_dag()` plus the ML steps, so you can compare the hand-written
orchestrator with the industry-standard one line by line. To run it, install Airflow 2.x, put this file in
your dags/ folder, and make the `sentinel` package importable by the workers.

What Airflow adds over the built-in runner: a scheduler, a web UI with run history, per-task retries and
SLAs, backfills over date ranges, sensors (wait for a file / a partition), pools to limit concurrency, and
distributed execution (Celery/Kubernetes executors).
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

default_args = {"owner": "data-platform", "retries": 2, "retry_delay": timedelta(minutes=5),
                "sla": timedelta(hours=1), "email_on_failure": True}


def _call(path: str, pass_run_id: bool = False):
    """Import lazily so the scheduler can parse the DAG without heavy imports."""
    def run(**context):
        module, fn = path.rsplit(".", 1)
        target = getattr(__import__(module, fromlist=[fn]), fn)
        return target(context["run_id"]) if pass_run_id else target()
    return run


with DAG(
    dag_id="sentinel_daily",
    start_date=datetime(2026, 5, 1),
    schedule="0 1 * * *",
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["sentinel", "fraud"],
) as dag:
    ingest = PythonOperator(task_id="ingest_stream_bronze", python_callable=_call("sentinel.platform.stream.ingest_transactions_stream"))
    cdc = PythonOperator(task_id="cdc_customers_scd2", python_callable=_call("sentinel.platform.cdc.run_cdc"))
    ref = PythonOperator(task_id="silver_reference", python_callable=_call("sentinel.platform.lakehouse.silver_reference", pass_run_id=True))
    s_tx = PythonOperator(task_id="silver_transactions", python_callable=_call("sentinel.platform.lakehouse.silver_transactions", pass_run_id=True))
    s_lab = PythonOperator(task_id="silver_labels", python_callable=_call("sentinel.platform.lakehouse.silver_labels", pass_run_id=True))
    s_comp = PythonOperator(task_id="silver_complaints", python_callable=_call("sentinel.platform.lakehouse.silver_complaints", pass_run_id=True))
    g_feat = PythonOperator(task_id="gold_fraud_features", python_callable=_call("sentinel.platform.lakehouse.gold_features", pass_run_id=True))
    g_agg = PythonOperator(task_id="gold_aggregates", python_callable=_call("sentinel.platform.lakehouse.gold_aggregates", pass_run_id=True))
    wh = PythonOperator(task_id="build_warehouse", python_callable=_call("sentinel.platform.warehouse.build_warehouse", pass_run_id=True))
    train = PythonOperator(task_id="train_models", python_callable=_call("sentinel.ml.train.train_all"))
    index = PythonOperator(task_id="rebuild_rag_index", python_callable=_call("sentinel.rag.index.build_index"))
    evals = PythonOperator(task_id="run_evals", python_callable=_call("sentinel.evals.rag_eval.run"))
    gate = PythonOperator(task_id="release_gate", python_callable=_call("sentinel.evals.gate.evaluate"))

    ingest >> s_tx
    [s_tx, cdc, ref, s_lab] >> g_feat >> train
    [s_tx, s_lab, s_comp] >> g_agg
    [s_tx, cdc, ref, s_lab] >> wh
    [train, index] >> evals >> gate
