"""Idempotent HTTPS uptime and gross-cost email alerts using the active gcloud login.

No access token is written to disk or printed. Budgets notify; they do not stop
services. Excluding credits makes trial-credit consumption visible to alerts.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

import httpx


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", required=True)
    p.add_argument("--hostname", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--output", required=True, help="private operator output outside the repository")
    p.add_argument("--budget-currency", default="USD")
    p.add_argument("--budget-units", type=int, default=250)
    args = p.parse_args()
    cli = shutil.which("gcloud.cmd") or shutil.which("gcloud")
    if not cli:
        raise SystemExit("gcloud is required")
    def cloud(*options):
        return subprocess.check_output([cli, *options], text=True).strip()
    token = cloud("auth", "print-access-token")
    client = httpx.Client(headers={"Authorization": "Bearer " + token,
                                  "x-goog-user-project": args.project}, timeout=45)
    def api(method, url, body=None):
        response = client.request(method, url, json=body)
        if response.status_code >= 400:
            message = response.json().get("error", {}).get("message", "request failed")
            raise RuntimeError(f"Google configuration HTTP {response.status_code}: {message[:700]}")
        return response.json()
    base = "https://monitoring.googleapis.com/v3/projects/" + args.project
    channels = api("GET", base + "/notificationChannels").get("notificationChannels", [])
    channel = next((c for c in channels if c.get("type") == "email" and
                    c.get("labels", {}).get("email_address") == args.email), None)
    if not channel:
        channel = api("POST", base + "/notificationChannels", {"type": "email",
            "displayName": "AutoML operator email", "labels": {"email_address": args.email}, "enabled": True})
    checks = api("GET", base + "/uptimeCheckConfigs").get("uptimeCheckConfigs", [])
    uptime = next((u for u in checks if u.get("displayName") == "AutoML public judge readiness"), None)
    if not uptime:
        uptime = api("POST", base + "/uptimeCheckConfigs", {
            "displayName": "AutoML public judge readiness",
            "monitoredResource": {"type": "uptime_url", "labels": {"project_id": args.project, "host": args.hostname}},
            "httpCheck": {"path": "/api/v1/v5/ready", "port": 443, "useSsl": True,
                          "validateSsl": True, "requestMethod": "GET"},
            "period": "300s", "timeout": "10s", "selectedRegions": ["USA", "EUROPE", "ASIA_PACIFIC"],
            "contentMatchers": [{"content": '"ready"\\s*:\\s*true', "matcher": "MATCHES_REGEX"}]})
    check_id = uptime["name"].split("/")[-1]
    policies = api("GET", base + "/alertPolicies").get("alertPolicies", [])
    policy = next((v for v in policies if v.get("displayName") == "AutoML readiness unavailable"), None)
    if not policy:
        policy = api("POST", base + "/alertPolicies", {
            "displayName": "AutoML readiness unavailable", "combiner": "OR", "enabled": True,
            "notificationChannels": [channel["name"]],
            "conditions": [{"displayName": "Readiness fails from multiple monitoring locations",
                "conditionThreshold": {
                    "filter": 'metric.type="monitoring.googleapis.com/uptime_check/check_passed" AND '
                              f'resource.type="uptime_url" AND metric.label.check_id="{check_id}"',
                    "comparison": "COMPARISON_LT", "thresholdValue": 1, "duration": "300s",
                    "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_FRACTION_TRUE"}],
                    "trigger": {"count": 2}}}],
            "documentation": {"mimeType": "text/markdown", "content":
                f"AutoML readiness is failing. Inspect the VM, Caddy, worker, verifier and sandbox control plane. https://{args.hostname}/repair"}})
    billing = json.loads(cloud("billing", "projects", "describe", args.project, "--format=json"))["billingAccountName"]
    number = cloud("projects", "describe", args.project, "--format=value(projectNumber)")
    budget_url = "https://billingbudgets.googleapis.com/v1/" + billing + "/budgets"
    budgets = api("GET", budget_url).get("budgets", [])
    budget = next((b for b in budgets if b.get("displayName") == "AutoML judging deployment before credits"), None)
    if not budget:
        budget = api("POST", budget_url, {
            "displayName": "AutoML judging deployment before credits",
            "budgetFilter": {"projects": ["projects/" + number], "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
                "customPeriod": {"startDate": {"year": 2026, "month": 10, "day": 30},
                                 "endDate": {"year": 2026, "month": 12, "day": 25}}},
            "amount": {"specifiedAmount": {"currencyCode": args.budget_currency, "units": str(args.budget_units)}},
            "thresholdRules": [{"thresholdPercent": v, "spendBasis": "CURRENT_SPEND"} for v in (.5, .8, 1)],
            "notificationsRule": {"monitoringNotificationChannels": [channel["name"]], "disableDefaultIamRecipients": True}})
    desired = {"specifiedAmount": {"currencyCode": args.budget_currency, "units": str(args.budget_units)}}
    if budget.get("amount") != desired:
        budget = api("PATCH", "https://billingbudgets.googleapis.com/v1/" + budget["name"] + "?updateMask=amount",
                     {"amount": desired})
    output = {"notification_channel": channel["name"], "channel_verification_status": channel.get("verificationStatus"),
              "uptime_check": uptime["name"], "outage_policy": policy["name"], "budget_name": budget["name"],
              "budget_currency": args.budget_currency, "budget_units": args.budget_units,
              "credits_excluded": True, "alerts_only": True}
    target = Path(args.output)
    target.write_text(json.dumps(output, indent=2) + "\n")
    target.chmod(0o600)
    print(json.dumps({"uptime_check_created": True, "email_channel_configured": True,
                      "budget_currency": args.budget_currency, "gross_cost_budget": args.budget_units, "alerts_only": True,
                      "channel_verification_status": channel.get("verificationStatus")}))


if __name__ == "__main__":
    main()
