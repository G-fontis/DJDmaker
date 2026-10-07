"""Static smoke wiring contract, not live or executable acceptance evidence."""
import ast
import inspect

from djd_maker.packaging import recovery_smoke


def test_recovery_smoke_runs_retention_gate_before_declaring_pass():
    tree = ast.parse(inspect.getsource(recovery_smoke.run_recovery_smoke))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == '_ready_failed_retention_smoke']
    assert len(calls) == 1
    passed = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and isinstance(node.value, ast.Constant) and node.value.value is True
              and any(isinstance(target, ast.Subscript)
                      and isinstance(target.slice, ast.Constant)
                      and target.slice.value == 'passed' for target in node.targets)]
    assert len(passed) == 1
    assert calls[0].lineno < passed[0].lineno


def test_retention_smoke_explicitly_distinguishes_synthetic_and_checks_safety():
    source = inspect.getsource(recovery_smoke._ready_failed_retention_smoke)
    tree = ast.parse(source)
    functions = {node.name for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    assert {'submit', 'upload_txt', 'delete_video_artifact'} <= functions
    returns = [node for node in ast.walk(tree) if isinstance(node, ast.Return)
               and isinstance(node.value, ast.Call)
               and isinstance(node.value.func, ast.Name) and node.value.func.id == 'dict']
    assert len(returns) == 1
    fields = {value.arg: value.value for value in returns[0].value.keywords}
    assert fields['live'].value is False
    assert fields['mode'].value == 'SYNTHETIC_REMOTE_READY_REAL_MEDIA'
    assertions = [ast.unparse(node.test) for node in ast.walk(tree)
                  if isinstance(node, ast.Assert)]
    assert "result.state is JobState.COMPLETED" in assertions
    assert "not result.safety_gate.failed_checks" in assertions
    assert "result.artifact_status == 'RETAINED'" in assertions
    assert "calls == before_repeat" in assertions
    assert "not source.exists()" in assertions


def test_sequential_smoke_acceptance_requires_retention_not_delete():
    # Exercise just the opt-in smoke's pure acceptance expressions: no Qt,
    # browser, media generation or claim of executable/live validation.
    from types import SimpleNamespace
    from djd_maker.packaging import sequential_smoke
    tree = ast.parse(inspect.getsource(sequential_smoke))
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    def expressions(name):
        return [node.value for node in assignments
                if any(isinstance(target, ast.Name) and target.id == name
                       for target in node.targets)]
    def evaluate(expression, namespace):
        return eval(compile(ast.Expression(expression), '<smoke-contract>', 'eval'),
                    namespace)
    calls = [['submit', f'release{i}'] for i in range(2)] + [
        ['download', f'release{i}'] for i in range(2)]
    bounded, isolation = expressions('actions_ok')
    context = dict(calls=calls,
                   jobs=[SimpleNamespace(id=f'release{i}', download_attempt_count=1) for i in range(2)],
                   fault={'isolation': True, 'overlay': True, 'attempts': 7},
                   stages={'save.deferred', 'save.summary', 'save.recovered'},
                   pipeline=SimpleNamespace(deferred_ids=set()))
    def accepted(values):
        actions_ok = evaluate(bounded, values)
        return evaluate(isolation, dict(values, actions_ok=actions_ok))
    assert accepted(context)
    assert not accepted(dict(context, calls=calls + [['delete', 'release0']]))
    assert not accepted(dict(context, calls=calls + [['submit', 'release0']]))
    retries = dict(context, calls=calls + [['download', 'release0']] * 2,
                   jobs=[SimpleNamespace(id='release0', download_attempt_count=3),
                         SimpleNamespace(id='release1', download_attempt_count=1)])
    assert accepted(retries)
    assert not accepted(dict(retries, jobs=[SimpleNamespace(id='release0', download_attempt_count=7),
                                           SimpleNamespace(id='release1', download_attempt_count=1)]))
    passed, = expressions('passed')
    predicates = [node for node in ast.walk(passed) if isinstance(node, ast.Compare)
                  and isinstance(node.left, ast.Attribute)
                  and node.left.attr == 'artifact_status']
    assert len(predicates) == 1
    assert ast.unparse(predicates[0]) == "j.artifact_status == 'RETAINED'"
