# PI-5 Bounded Delegation

PI-5 adds host-mediated delegation requests while PAO remains the authority for routing, quota, execution isolation, verification, and task truth.

PI-5A is non-executing. It adds:

- a typed request contract;
- a maximum of three requests per parent run;
- worker-supplied intent and reason only;
- a trusted Pi tool that records structured requests but starts no worker;
- fail-closed extraction from Pi JSON tool lifecycle events.

PI-5A does not enable the tool in production Pi argv and does not launch child work. PI-5B will add explicit feature-gated loading plus a host broker for isolated child execution.
