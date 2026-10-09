---
manual_triage_base_minutes: 180
---

# Default Fallback Runbook

## Symptoms
- Error pattern does not match any known runbook

## Root Cause Candidates
- Unknown failure mode or transient infrastructure issue
- Service discovery / DNS resolution failure
- Configuration drift

## Remediation
1. Review correlated logs and traces for context
2. Check if the error is transient (auto-recover within 60s)
3. If persistent, escalate to on-call SRE for manual investigation
4. No automated remediation available — manual review required
