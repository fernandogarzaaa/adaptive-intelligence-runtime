"""AIR self-audit harness (AIR-SELF-AUDIT v1).

The runtime audits itself and reports findings. It NEVER modifies AIR:

- Every probe runs against a FRESH, ISOLATED AIR instance rooted in a
  temporary directory created by the harness. The audited instance is
  thrown away after the run. The harness never opens the operator's
  real database and never touches ``AIR_DATA_DIR``.
- The harness performs only the adversarial *actions* its probes
  require (denied tool calls, poisoned tool output, fabricated
  evidence) on that throwaway instance. Those actions are the test;
  they are not repairs, patches, or configuration changes.
- Report output is confined to the ``--out`` directory given on the
  command line. The audit writes no ``air`` source file, no migration,
  no config file, anywhere. ``tests/test_self_audit.py`` enforces
  this structurally: it hashes every file under ``src/air/`` before
  and after a full audit run and fails if any byte changed.

Findings are data. Nothing in this package applies them.
"""

from air.audit.schema import AuditReport, Finding  # noqa: F401
