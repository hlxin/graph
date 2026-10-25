import pytest

from graph_runtime import Projection

NODES = [{"id": "a", "types": ["Sample"]}, {"id": "b", "types": ["Sample"]}, {"id": "c", "types": ["Catalyst"]},
         {"id": "d", "types": ["Species", "Catalyst"]}, "e"]
EDGES = [{"id": "e1", "source": "a", "target": "b", "relation": "USES"},
         {"id": "e2", "source": "b", "target": "c", "relation": "USES"},
         {"id": "e3", "source": "a", "target": "c", "relation": "CITES"},
         {"id": "e4", "source": "c", "target": "d", "relation": "USES"},
         {"id": "e5", "source": "c", "target": "d", "relation": "USES"},       # parallel to e4
         {"id": "e6", "source": "d", "target": "a", "relation": "CITES"}]      # closes a cycle a-b-c-d-a


@pytest.fixture
def small():
    """A small graph: a chain a-b-c-d over USES with a parallel pair c-d, a CITES shortcut a-c and a CITES cycle
    back d-a; e is isolated."""
    return Projection.build(nodes=NODES, edges=EDGES, bindings={"source": "doc@1", "schema": "s@1", "policy": "public"})
