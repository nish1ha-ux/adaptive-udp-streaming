"""
Adaptive UDP Streaming — Flask Web Dashboard Application
=========================================================

Author: Nishtha Bhushan
Module: src/dashboard/app.py

Provides a modern REST API and web user interface for real-time
monitoring and management of UDP file streaming transfers.
"""

import argparse
import logging
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request

from src.dashboard.transfer_engine import TransferEngine

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def create_app(workspace_root: Optional[Path] = None) -> Flask:
    """Application factory for the Adaptive UDP Streaming web dashboard."""
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )

    # Initialize backend engine
    engine = TransferEngine(workspace_root=workspace_root)
    app.config["TRANSFER_ENGINE"] = engine

    @app.route("/")
    def index():
        """Render the main single-page dashboard."""
        branch = engine.get_current_branch()
        return render_template("index.html", branch=branch)

    @app.route("/api/status", methods=["GET"])
    def get_status():
        """Return overall system status, capabilities, and active transfer state."""
        return jsonify(engine.get_status())

    @app.route("/api/files", methods=["GET"])
    def list_files():
        """List safe local sample files available for transfer testing."""
        files = engine.list_selectable_files()
        return jsonify({"files": files})

    @app.route("/api/transfer/start", methods=["POST"])
    def start_transfer():
        """Initiate a UDP transfer in the background."""
        data = request.get_json(silent=True) or {}
        file_path = data.get("file_path", "")
        host = data.get("host", "127.0.0.1")
        port = data.get("port", 5005)
        pacing = data.get("pacing_interval", 0.001)
        timeout = data.get("timeout", 15.0)
        post_end_timeout = data.get("post_end_timeout", 3.0)

        try:
            port = int(port)
            pacing = float(pacing)
            timeout = float(timeout)
            post_end_timeout = float(post_end_timeout)
        except (ValueError, TypeError) as exc:
            return jsonify({"success": False, "error": f"Invalid numerical parameter: {exc}"}), 400

        success, msg = engine.start_transfer(
            file_path_str=file_path,
            host=host,
            port=port,
            pacing_interval=pacing,
            timeout=timeout,
            post_end_timeout=post_end_timeout,
        )

        status_code = 200 if success else 400
        return jsonify({"success": success, "message": msg}), status_code

    @app.route("/api/transfer/cancel", methods=["POST"])
    def cancel_transfer():
        """Cancel an ongoing UDP transfer."""
        success, msg = engine.cancel_transfer()
        status_code = 200 if success else 400
        return jsonify({"success": success, "message": msg}), status_code

    @app.route("/api/transfer/progress", methods=["GET"])
    def get_progress():
        """Return live transfer telemetry and verification progress."""
        status_data = engine.get_status()
        return jsonify(status_data["transfer"])

    @app.route("/api/metrics/history", methods=["GET"])
    def get_metrics():
        """Return time-series telemetry data points for charting."""
        return jsonify({"history": engine.get_metrics_history()})

    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        """Return timestamped transfer logs."""
        limit = request.args.get("limit", 100, type=int)
        return jsonify({"logs": engine.get_logs(limit=limit)})

    @app.route("/api/logs/clear", methods=["POST"])
    def clear_logs():
        """Clear the current log buffer."""
        engine.clear_logs()
        return jsonify({"success": True})

    @app.route("/api/network/conditions", methods=["GET", "POST"])
    def network_conditions():
        """Query or configure network conditions."""
        if request.method == "POST":
            # Inform user accurately that emulator is on Subham's branch
            return jsonify({
                "success": False,
                "supported": False,
                "message": (
                    "Network impairment emulation is assigned to Subham Kumar Sahu "
                    "on branch 'feature/rate-control-and-emulator' and is not merged into "
                    "this baseline UDP packet branch ('feature/nishtha-udp-packet'). "
                    "All transfers currently run over direct local socket."
                ),
            }), 400

        return jsonify({
            "supported": False,
            "status": "Branch Pending (Subham's emulator module)",
            "configured_loss": 0.0,
            "configured_delay_ms": 0.0,
            "configured_jitter_ms": 0.0,
            "configured_seed": None,
        })

    @app.route("/api/experiments", methods=["GET"])
    def get_experiments():
        """Return benchmark experiment results (E1–E4)."""
        return jsonify({"experiments": engine.get_experiment_benchmarks()})

    @app.route("/api/experiments/export.csv", methods=["GET"])
    def export_experiments_csv():
        """Download benchmark experiment results as CSV."""
        csv_data = engine.export_experiments_csv()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=experiment_results.csv"},
        )

    return app


def main():
    """Command-line entry point to launch the dashboard web server."""
    parser = argparse.ArgumentParser(description="Adaptive UDP Streaming Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port to listen on (default: 8080)")
    parser.add_argument("--debug", action="store_true", help="Run Flask in debug mode")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  [%(name)s]  %(message)s",
    )

    app = create_app()
    print(f"\n" + "=" * 70)
    print(f"[*] Adaptive UDP Streaming Dashboard running at:")
    print(f"   http://{args.host}:{args.port}")
    print(f"   Active Branch: feature/nishtha-udp-packet")
    print(f"=" * 70 + "\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
