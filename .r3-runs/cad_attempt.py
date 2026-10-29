import json, sys, time
from r3_model.project import Project
from r3_model.diagnostics import StageResult
from r3_onshape.client import NativeBridgeTransport
from r3_onshape.sync import sync, write_cad_model
label = sys.argv[1]
project = Project.load("examples/robots/rc-car")
api = NativeBridgeTransport()
result = StageResult(stage="cad")
t0 = time.time()
report = sync(project, api, result, attempt_label=label)
result.duration_s = time.time() - t0
if result.ok:
    write_cad_model(project, report, api.name)
print("doc", report.document and report.document.url(), "status", report.feature_status)
for e in report.feature_errors: print("FEATURE_ERR", e[:800])
for d in result.diagnostics: print(d.severity.value, d.code, d.message, json.dumps(d.data)[:1500])
result.write(project.build_dir("cad") / f"attempt_{label}.json")
