"""Developer-note §§1–6: transport, limits, provenance and independently derived expectations."""

import copy
import json
from fractions import Fraction

import jsonschema
import pytest

from graph_runtime import (CONTRACT, CandidateError, HypothesisCandidate, Projection, ProjectionError, RequestError,
                           Result, execute, json_schema, normalize_request, staleness, validate_candidate, witness_refs)
from graph_runtime._canonical import digest
from graph_runtime._validation import shape_problems
from graph_runtime.conformance import check_case, load_cases, run_conformance
from oracle import (bottleneck, closed_nodes, derivation_values, expand_scope, least_cost, least_depths,
                    lift_periodic, node_id, pattern_rows, regular_ends, support_answer, typed_ends)
from test_hypotheses import TESTS


def reseal(data):
    data = copy.deepcopy(data)
    data['result_id'] = digest({k: v for k, v in data.items() if k not in ('result_id', 'timing')})
    return data


def graph():
    return Projection.build(nodes=['a', 'b', 'z'], edges=[
        {'id': 'e1', 'source': 'z', 'target': 'b', 'relation': 'R'},
        {'id': 'e2', 'source': 'b', 'target': 'a', 'relation': 'R'}], relations=['R', 'EMPTY'])


def result(op='reachability', **extra):
    p = graph()
    args = {'sources': ['z'], 'scope': [('R', 'forward')]}
    if op == 'shortest_path':
        args.update(targets=['a'], costs={'by_relation': {'R': 1}})
    return execute(p, p.request(op, **{**args, **extra}))


def candidate(**extra):
    return HypothesisCandidate.create(**{
        'statement': 's', 'scope': 'synthetic', 'projection': graph(), 'producer': {'name': 'p', 'version': '1'},
        'basis': [('node', 'z')], 'proposed_tests': TESTS, 'at': '2026-10-25', **extra})


@pytest.mark.parametrize('field,value', [
    ('contract', 'another-contract'), ('operation', 'unknown'), ('request', None), ('mode', None),
    ('answer', {}), ('error', []), ('interruption', []), ('complete', 1), ('timing', {'total_s': -1}),
    ('stats', {'expansions': True, 'edges_examined': 0, 'discovered': 0}),
])
def test_result_reader_and_schema_reject_bad_shapes(field, value):
    data = reseal({**result().to_dict(), field: value})
    assert not jsonschema.Draft202012Validator(json_schema('result')).is_valid(data)
    assert shape_problems('result', data)
    with pytest.raises(ValueError):
        Result.from_dict(data)


@pytest.mark.parametrize('change', [
    lambda d: d.update(request_id='sha256:' + '0' * 64),
    lambda d: d.update(projection='sha256:' + '0' * 64),
    lambda d: d['request'].update(sources=['z', 'b']),
    lambda d: d['answer'].update(count=99),
    lambda d: d['answer']['reached'].reverse(),
    lambda d: d['answer']['reached'][0].update(depth=1),
    lambda d: d['stats'].update(expansions=1_000_001),
])
def test_result_reader_rejects_inconsistent_transport(change):
    data = result().to_dict()
    change(data)
    with pytest.raises(ValueError):
        Result.from_dict(reseal(data))


@pytest.mark.parametrize('field,value', [('found', 1), ('optimal', True), ('witness', None)])
def test_interrupted_shortest_shape_is_honest(field, value):
    p = Projection.build(nodes=['m', 's', 't'], edges=[
        {'id': 'd', 'source': 's', 'target': 't', 'relation': 'R'},
        {'id': 'h', 'source': 's', 'target': 'm', 'relation': 'R'}])
    data = execute(p, p.request('shortest_path', sources=['s'], targets=['t'], scope=[('R', 'forward')],
                               costs={'by_edge': {'d': 10, 'h': 1}}, limits={'max_expansions': 1})).to_dict()
    assert data['status'] == 'interrupted' and data['answer']['found'] is True
    data['answer'][field] = value
    data = reseal(data)
    assert not jsonschema.Draft202012Validator(json_schema('result')).is_valid(data)
    with pytest.raises(ValueError):
        Result.from_dict(data)


@pytest.mark.parametrize('field,value', [('cost', 1), ('cost_exact', '2/0'), ('lower_bound', 99)])
def test_exact_cost_and_bound_semantics_on_readback(field, value):
    data = result('shortest_path', costs={'by_relation': {'R': 0.5}}, limits={'max_expansions': 1}).to_dict()
    data['answer'][field] = value
    with pytest.raises(ValueError):
        Result.from_dict(reseal(data))


@pytest.mark.parametrize('change', [
    lambda d: d.pop('identity'), lambda d: d.pop('bindings'), lambda d: d.update(nodes=None),
    lambda d: d.update(extra=True), lambda d: d.update(bindings={'': 'x'}),
])
def test_snapshot_reader_enforces_schema_before_rebuilding(change):
    data = graph().to_dict()
    change(data)
    assert not jsonschema.Draft202012Validator(json_schema('projection')).is_valid(data)
    with pytest.raises(ProjectionError) as error:
        Projection.from_dict(data)
    assert error.value.code == 'invalid_snapshot'


def test_empty_snapshot_roundtrip_and_missing_source_refusal():
    p = Projection.build(nodes=[], edges=[], relations=['R'])
    assert Projection.from_dict(p.to_dict()) == p
    r = execute(p, p.request('reachability', sources=['missing'], scope=[('R', 'forward')]))
    assert r.status == 'failed' and r.error['code'] == 'unknown_node'


@pytest.mark.parametrize('kind', ['by_edge', 'by_relation'])
def test_declared_unused_relation_needs_no_edge_cost(kind):
    p = graph()
    r = execute(p, p.request('shortest_path', sources=['z'], targets=['a'], scope=[('EMPTY', 'forward')],
                            costs={kind: {}}))
    assert r.complete and r.found is False


def test_canonical_answer_order_and_source_discovery_counts():
    r = result()
    assert [e['node'] for e in r.answer['reached']] == ['a', 'b', 'z']
    assert [h['edge'] for h in r.path_to('a')] == ['e1', 'e2']
    r = result(sources=['z', 'b'], limits={'max_results': 0})
    assert r.status == 'interrupted' and r.answer['count'] == 0 and r.stats['discovered'] == 2
    p = graph()
    r = execute(p, p.request('dependency_impact', changed=['z'], scope=[('R', 'forward')], max_depth=0))
    assert r.complete and r.answer['count'] == 0 and r.stats['discovered'] == 1


@pytest.mark.parametrize('control', ['timeout', 'deadline'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '1'])
def test_invalid_execution_controls_are_refused(control, value):
    p = graph()
    with pytest.raises(ValueError):
        execute(p, p.request('reachability', sources=['z'], scope=[('R', 'forward')]), **{control: value})


def test_clock_failure_is_a_failed_result():
    p = graph()
    def broken():
        raise RuntimeError('clock unavailable')
    r = execute(p, p.request('reachability', sources=['z'], scope=[('R', 'forward')]), timeout=1, clock=broken)
    assert r.status == 'failed' and r.error['code'] == 'internal_error'


def test_unrepresentable_invalid_numbers_are_request_errors():
    with pytest.raises(RequestError) as error:
        normalize_request({'operation': 'reachability', 'projection': graph().identity, 'sources': ['z'],
                           'scope': [('R', 'forward')], 'max_depth': -(10**5000)})
    assert error.value.code == 'invalid_request'


@pytest.mark.parametrize('bearing', [[], {}, 1, None])
def test_malformed_candidate_bearing_is_reported(bearing):
    data = candidate().to_dict()
    data['proposed_tests'][0]['outcomes'][0]['bearing'] = bearing
    assert any(p['code'] == 'invalid_bearing' for p in validate_candidate(data))
    with pytest.raises(CandidateError):
        HypothesisCandidate.from_dict(data)


def test_large_finite_score_and_extension_keys_survive_transport():
    c = candidate().add_score(value=10**400, method='m', method_version='1', candidate_set='s',
                              direction='higher_first')
    assert HypothesisCandidate.from_json(c.to_json()).scores[0]['value'] == 10**400
    with pytest.raises(CandidateError):
        candidate(extensions={1: 'lost', '1': 'retained'})
    data = candidate().to_dict()
    data['extensions'] = {1: 'lost', '1': 'retained'}
    assert validate_candidate(data)


@pytest.mark.parametrize('created,withdrawn', [
    ('2026-10-25T12:00', '2026-10-25T11:00'),
    ('2026-10-25T12:00:00.000000002Z', '2026-10-25T12:00:00.000000001Z'),
])
def test_lifecycle_chronology_keeps_full_timestamp_precision(created, withdrawn):
    with pytest.raises(CandidateError) as error:
        candidate(at=created).withdraw(at=withdrawn, reason='scope changed')
    assert error.value.code == 'invalid_time'


@pytest.mark.parametrize('at', ['2026-10-25T10:00+00:60', '2026-10-25\n'])
def test_bad_timestamp_rejected_in_runtime_and_schema(at):
    data = candidate().to_dict()
    data['lifecycle'][0]['at'] = at
    assert validate_candidate(data)
    assert not jsonschema.Draft202012Validator(json_schema('hypothesis_candidate')).is_valid(data)


def test_earliest_calendar_timestamp_does_not_overflow_in_chronology():
    c = candidate(at='0001-01-01T00:00+23:00')
    assert c.withdraw(at='0001-01-01', reason='scope changed').state == 'withdrawn'


def test_assessment_and_json_errors_are_candidate_errors():
    with pytest.raises(CandidateError):
        candidate().attach_assessment(stance='opposing', evidence=[('external', 'x')], conditions='\ud800',
                                      assessor='a', at='2026-10-25')
    with pytest.raises(CandidateError):
        HypothesisCandidate.from_json('{bad json')


@pytest.mark.parametrize('ref', [('node', 'missing'), ('edge', 'missing'), ('result', 'sha256:' + '0' * 64)])
def test_snapshot_staleness_cannot_clear_unresolved_references(ref):
    state = staleness(candidate(basis=[ref]), projection=graph())
    assert state['stale'] is None and state['undecided']


def test_a_new_binding_is_not_confused_with_an_absent_binding():
    p = graph()
    assert p.stale_bindings({'context': None}) == ('context',)
    r = execute(p, p.request('reachability', sources=['z'], scope=[('R', 'forward')]),
                current_bindings={'context': None})
    assert r.status == 'stale_snapshot' and r.answer is None


def test_zero_hop_witness_keeps_its_node_provenance():
    r = result('shortest_path', targets=['z'])
    assert witness_refs(r) == [{'type': 'result', 'ref': r.result_id}, {'type': 'node', 'ref': 'z'}]
    r = result(max_depth=0)
    assert witness_refs(r, 'z')[1:] == [{'type': 'node', 'ref': 'z'}]


def test_conformance_binds_the_case_not_just_a_valid_result():
    suite = load_cases()
    case = next(c for c in suite['cases'] if c['name'] == 'reachability-forward')
    p = Projection.build(**suite['graphs'][case['graph']])
    # This request gives the same reached nodes/depths but is still the wrong request.
    raw = {**case['request'], 'projection': p.identity, 'max_depth': 3}
    data = execute(p, raw).to_dict()
    assert check_case(case, data, suite['graphs'][case['graph']])


def test_handwritten_conformance_answers_agree_with_independent_oracles():
    suite = load_cases()
    assert suite['contract'] == CONTRACT
    for case in suite['cases']:
        exp = case['expect']
        if 'error_code' in exp:
            continue
        g, r = suite['graphs'][case['graph']], case['request']
        op = r['operation']
        if 'dimensions' in g and op != 'periodicity':  # a periodic cell: the oracle reads a lift that holds the search
            seeds = r.get('sources') or r.get('changed')
            g = lift_periodic(g, seeds, (r.get('max_depth') or 0) + 1)
        rp, relations = g.get('relation_parents') or {}, g.get('relations') or []
        if 'scope' in r:                          # hierarchies written out by the oracle, not by the runtime
            r = {**r, 'scope': expand_scope(r['scope'], rp, relations)}
        if 'pattern' in r:
            r = {**r, 'pattern': [{**pos, 'scope': expand_scope(pos['scope'], rp, relations)} for pos in r['pattern']]}
        g = {**g, 'nodes': closed_nodes(g['nodes'], g.get('type_parents') or {})}
        if op in ('reachability', 'dependency_impact'):
            strict = op == 'dependency_impact'
            want = least_depths(g['edges'], r['changed' if strict else 'sources'], r['scope'],
                                r.get('max_depth'), strict)
            if exp['status'] == 'completed':
                assert exp['affected' if strict else 'reached'] == want, case['name']
            else:
                assert exp.get('affected' if strict else 'reached', want) == want, case['name']
        elif op == 'typed_path':
            want = bool(typed_ends(g['nodes'], g['edges'], r['sources'], r['pattern'], r.get('targets')))
            assert exp['found'] is want, case['name']
        elif op == 'support':
            raw, graph = case['request'], suite['graphs'][case['graph']]     # the oracle expands the hierarchies
            want = support_answer(graph['nodes'], graph['edges'], raw['failed'], raw['scope'], raw.get('all_of'),
                                  graph.get('type_parents'), rp, relations)
            if 'counts' in exp:
                assert exp['counts'] == want['counts'], case['name']
            for key in ('lost', 'weakened'):
                if key in exp:
                    assert exp[key] == want[key], case['name']
        elif op == 'bottleneck_path':
            want = bottleneck(g['nodes'], g['edges'], r['sources'], r['targets'], r['scope'], r['weights'],
                              r.get('objective', 'max_min'))
            if 'found' in exp:
                assert exp['found'] is (want is not None), case['name']
            if 'value' in exp:
                assert want[0] == exp['value'] and len(exp['witness_edges']) == want[1], case['name']
        elif op == 'regular_path':
            raw, graph = case['request'], suite['graphs'][case['graph']]     # the oracle expands the hierarchies
            path = normalize_request({**raw, 'projection': 'sha256:' + '0' * 64}).to_dict()['path']
            want = regular_ends(graph['nodes'], graph['edges'], raw['sources'], path, raw.get('targets'),
                                graph.get('type_parents'), rp, relations)
            if 'found' in exp:
                assert exp['found'] is bool(want), case['name']
            if 'witness_edges' in exp and want:
                assert len(exp['witness_edges']) == min(want.values()), case['name']
            if 'path_reached' in exp:
                assert exp['path_reached'] == want, case['name']
        elif op == 'derivation':
            raw, graph = case['request'], suite['graphs'][case['graph']]     # the oracle expands the hierarchies
            values = derivation_values(graph['nodes'], graph['edges'], raw['scope'], raw.get('all_of'),
                                       raw.get('grounds'), raw.get('costs'), raw.get('objective', 'min_sum'),
                                       graph.get('type_parents'), rp, relations)
            if case['expect']['status'] == 'completed':
                assert exp['derived'] == {t: values[t] for t in raw['targets'] if t in values}, case['name']
                assert exp['underived'] == [t for t in sorted(raw['targets']) if t not in values], case['name']
            else:
                assert all(values[t] == v for t, v in exp['derived'].items()), case['name']
        elif op == 'personalized_pagerank':
            from test_ranking import exact_pagerank
            raw, graph = case['request'], suite['graphs'][case['graph']]
            pi = exact_pagerank(graph['nodes'], graph['edges'], raw['sources'], raw['scope'],
                                Fraction(raw.get('alpha', '3/20')))
            assert all(pi[n] == Fraction(v) for n, v in exp['pagerank'].items()), case['name']
            ranked = sorted((n for n in pi if pi[n]), key=lambda n: (-pi[n], n))
            assert set(exp['pagerank']) == set(ranked[:raw.get('top', 20)]), case['name']
        elif op == 'periodicity':
            from test_periodicity import oracle as lattice_oracle
            graph = suite['graphs'][case['graph']]
            names = [node_id(n) for n in graph['nodes']]
            for seeds, rank, basis, copies in exp['components']:
                want, reached = lattice_oracle(names, graph['edges'], graph['dimensions'], seeds[0], 12)
                assert [list(r) for r in want] == basis and set(seeds) <= reached, case['name']
        elif op in ('components', 'biconnectivity'):
            import networkx as nx
            pairs = {(s['relation'], s['direction']) for s in r['scope']}
            names = [node_id(n) for n in g['nodes']]
            hops = nx.MultiDiGraph()
            hops.add_nodes_from(names)
            for e in g['edges']:
                if (e['relation'], 'forward') in pairs:
                    hops.add_edge(e['source'], e['target'], key=e['id'])
                if (e['relation'], 'reverse') in pairs:
                    hops.add_edge(e['target'], e['source'], key=e['id'])
            links = [e for e in g['edges'] if (e['relation'], 'forward') in pairs or (e['relation'], 'reverse') in pairs]
            if op == 'components':
                parts = sorted(sorted(c) for c in (nx.strongly_connected_components(hops) if r.get('connectivity') == 'strong'
                                                   else nx.weakly_connected_components(hops)))
                chosen = [c for c in parts if not r.get('nodes') or set(r['nodes']) & set(c)]
                assert exp['component_sets'] == [c for c in chosen if len(c) >= r.get('min_size', 1)], case['name']
                if exp['status'] == 'completed':
                    assert exp['total'] == len(chosen), case['name']
                for first, flags in exp.get('component_flags', {}).items():
                    c = set(next(x for x in parts if x[0] == first))
                    assert flags == [len(c) > 1 or any(hops.has_edge(u, u) for u in c),
                                     all(u in c for v in c for u in hops.predecessors(v)),
                                     all(v in c for u in c for v in hops.successors(u))], case['name']
                for first, count in exp.get('component_edges', {}).items():
                    c = set(next(x for x in parts if x[0] == first))
                    assert count == len({k for u, v, k in hops.edges(keys=True) if u in c and v in c}), case['name']
            else:
                def pieces(skip=None, without=None):
                    h = nx.MultiGraph()
                    h.add_nodes_from(n for n in names if n != without)
                    h.add_edges_from((e['source'], e['target']) for e in links
                                     if e is not skip and without not in (e['source'], e['target']))
                    return nx.number_connected_components(h)
                base, whole = pieces(), nx.MultiGraph(hops.to_undirected())
                region = set(names) if not r.get('nodes') else set().union(
                    *(nx.node_connected_component(whole, n) for n in r['nodes']))
                points = {v: pieces(without=v) - base + 1 for v in sorted(region) if pieces(without=v) - base + 1 > 1}
                assert exp['articulation'] == points, case['name']
                assert exp['bridge_edges'] == sorted(e['id'] for e in links if e['source'] in region
                                                     and e['source'] != e['target'] and pieces(skip=e) > base)
                if 'component_count' in exp:
                    assert exp['component_count'] == len({frozenset(nx.node_connected_component(whole, n))
                                                          for n in region}), case['name']
                if 'block_edges' in exp:
                    simple = nx.Graph((e['source'], e['target']) for e in links if e['source'] != e['target'])
                    owner = {frozenset(uv): i for i, b in enumerate(nx.biconnected_component_edges(simple)) for uv in b}
                    blocks: dict = {}
                    for e in links:
                        if e['source'] != e['target'] and e['source'] in region:
                            blocks.setdefault(owner[frozenset((e['source'], e['target']))], []).append(e['id'])
                    assert exp['block_edges'] == sorted(sorted(b) for b in blocks.values()), case['name']
        elif op == 'pattern':
            raw = case['request']             # the brute force expands the hierarchies itself
            rows = pattern_rows(suite['graphs'][case['graph']]['nodes'], g['edges'], raw['match'],
                                raw.get('steps', []), suite['graphs'][case['graph']].get('type_parents'), rp,
                                relations)
            assert exp['total'] == len(rows), case['name']
            if exp.get('rows') and raw.get('answer', 'rows') == 'rows':
                assert exp['rows'] == [[b, links] for b, links in rows], case['name']
        else:
            cost = least_cost(g['nodes'], g['edges'], r['sources'], r['targets'], r['scope'], r['costs'])
            if 'found' in exp:
                assert exp['found'] is (cost is not None), case['name']
            if 'cost' in exp:
                assert cost == exp['cost'], case['name']
            if 'cost_exact' in exp:
                assert cost == Fraction(exp['cost_exact']), case['name']


def test_benchmark_parity_includes_isolated_nodes(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'benchmarks' / 'bench.py'
    spec = importlib.util.spec_from_file_location('graph_benchmark', path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    nodes = [{'id': 'a', 'types': []}, {'id': 'b', 'types': []}]
    p = Projection.build(nodes=nodes, edges=[], relations=['R'])
    for raw in [
        {'operation': 'reachability', 'sources': ['a'], 'scope': [('R', 'forward')]},
        {'operation': 'dependency_impact', 'changed': ['a'], 'scope': [('R', 'reverse')]},
        {'operation': 'shortest_path', 'sources': ['a'], 'targets': ['b'], 'scope': [('R', 'forward')],
         'costs': {'by_edge': {}}},
    ]:
        r = execute(p, {**raw, 'projection': p.identity})
        assert bench.parity(raw, r, nodes, [], {}) == {'checked': True, 'agrees': True}
    report = {'started': '2026-10-25T12:00:00Z', 'workloads': [
        {'size': 'small', 'build_s': 0, 'requests': [
            {'operation': 'reachability', 'status': 'completed', 'stats': {'expansions': 0},
             'timing': dict.fromkeys(['bind_s', 'search_s', 'witness_s', 'result_s'], 0), 'json_s': 0,
             'read_s': 0, 'verify_s': 0, 'verified': True,
             'parity': {'checked': False, 'why': 'networkx not installed'}}]}]}
    monkeypatch.setattr(bench, 'run', lambda sizes, seed: report)
    monkeypatch.setattr(bench, 'HERE', tmp_path)
    assert bench.main(['--sizes', 'small']) == 1


@pytest.mark.parametrize('field', ['nodes', 'edges'])
@pytest.mark.parametrize('bad', ['ab', {}, None, 3])
def test_projection_collections_are_explicit(field, bad):
    with pytest.raises(ProjectionError) as error:
        Projection.build(**{'nodes': [], 'edges': [], field: bad})
    assert error.value.code == 'invalid_record'


def test_result_membership_index_is_immutable():
    r = result()
    assert r.reachable('a') is True
    with pytest.raises(TypeError):
        r._entries()['a'] = None
    assert r.reachable('a') is True


def test_zero_hop_witness_must_name_an_input_node():
    from graph_runtime import verify_result
    data = result('shortest_path', targets=['z']).to_dict()
    data['request'].update(sources=['missing'], targets=['missing'])
    data['request_id'] = normalize_request(data['request']).request_id
    data['answer']['witness'].update(source='missing', target='missing')
    data = reseal(data)
    assert verify_result(data, nodes=['a', 'b', 'z'], edges=[])


def test_conformance_checks_the_canonical_tied_witness():
    def different_tie(p, request, **ctx):
        data = execute(p, request, **ctx).to_dict()
        witness = (data.get('answer') or {}).get('witness')
        if witness:
            for hop in witness['hops']:
                if hop['edge'] == 'e4':
                    hop['edge'] = 'e5'
        return reseal(data)
    assert run_conformance(different_tie)['failures']


def test_runtime_imports_only_the_standard_library():
    import ast
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'src' / 'graph_runtime'
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text())
        top = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module.split('.')[0]]
            else:
                continue
            # the optional retrieval module alone needs numpy (an extra), imported where it is used, never on import
            optional = {'numpy'} if path.name == 'retrieval.py' and id(node) not in top else set()
            assert all(m in sys.stdlib_module_names or m in optional for m in modules), (path, node.lineno, modules)
            assert not {'scale', 'states', 'rates', 'cathanor'} & set(modules)


def test_the_runtime_never_loads_the_optional_retrieval_module_or_numpy():
    import subprocess
    import sys
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / 'src'
    code = ("import sys, graph_runtime, graph_runtime.conformance; "
            "print(sorted(m for m in ('numpy', 'graph_runtime.retrieval') if m in sys.modules))")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                         env={'PYTHONPATH': str(src)})
    assert out.stdout.strip() == '[]'


def test_bundled_shape_checks_match_independent_jsonschema_on_mutations():
    p = graph()
    support = execute(p, p.request('support', failed=['a'], scope=[('R', 'forward')])).to_dict()
    records = [('projection', graph().to_dict()), ('request', result().to_dict()['request']),
               ('result', result().to_dict()), ('result', result('shortest_path').to_dict()),
               ('result', support), ('request', support['request']),
               ('hypothesis_candidate', candidate().to_dict())]
    replacements = [None, True, 0, 1.0, '', [], {}]

    def locations(value, path=()):
        yield path
        children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
        for key, child in children:
            yield from locations(child, (*path, key))

    for entry, record in records:
        validator = jsonschema.Draft202012Validator(json_schema(entry))
        for path in locations(record):
            for value in replacements:
                changed = copy.deepcopy(record)
                if not path:
                    changed = value
                else:
                    parent = changed
                    for key in path[:-1]:
                        parent = parent[key]
                    parent[path[-1]] = value
                assert (not shape_problems(entry, changed)) == validator.is_valid(changed), (entry, path, value)
