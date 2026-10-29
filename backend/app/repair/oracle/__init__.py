"""Independent oracles. Only these modules write CodeReceipt / MLReceipt.

They never trust a self-report from the agent or from candidate code: every verdict is
computed from commands the oracle runs itself in a fresh environment built from the
pristine base snapshot plus the frozen candidate diff.
"""
