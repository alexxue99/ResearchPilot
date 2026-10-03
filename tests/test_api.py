import tempfile
import time
import unittest
import base64
import io
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from researchpilot.api import create_app
from researchpilot.jobs import DurableJobQueue
from researchpilot.storage import ResearchRepository
from researchpilot.models import Status, Source, Conjecture, ExperimentDesign, ExperimentResult, ExperimentalEvidence


class ApiTests(unittest.TestCase):
    def test_experiment_edit_rerun_and_resynthesis(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'RESEARCHPILOT_WORKERS': '0', 'RESEARCHPILOT_EXECUTOR': 'docker'}):
            with TestClient(create_app(tmp)) as client:
                created = client.post('/research', json={'question': 'Does treatment improve the outcome?'}).json()
                research_id = created['id']
                repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
                state = repo.get(research_id)
                state.conjecture = Conjecture(state.question, state.question)
                state.status = Status.COMPLETED
                state.report = 'Old report'
                state.experiments_planned.append(ExperimentDesign(
                    'design-1', 'Trial', 'Treatment helps', [], [], [], [], [], {}, [], 'Improvement', [],
                    code="print('old')", visualization_code="print('old chart')"))
                state.experiments_completed.append(ExperimentResult(
                    'run-old', 'design-1', 'completed', {}, {'control': [1, 2, 3]},
                    artifacts=[str(Path(tmp) / 'artifacts' / 'old.svg')]))
                state.experimental_evidence.append(ExperimentalEvidence('run-old', 'Old finding', 'supports'))
                state.artifacts.append(str(Path(tmp) / 'artifacts' / 'old.svg'))
                repo.save(state)

                edited = client.put(f'/research/{research_id}/experiments/design-1/code',
                                    json={'code': "print('new')"})
                self.assertEqual(edited.status_code, 200, edited.text)
                self.assertEqual(edited.json()['experiments_planned'][0]['code'], "print('new')")
                self.assertEqual(edited.json()['experiments_planned'][0]['visualization_code'], '')
                self.assertEqual(edited.json()['experiments_completed'], [])
                self.assertEqual(edited.json()['experimental_evidence'], [])
                self.assertEqual(edited.json()['artifacts'], [])
                self.assertEqual(client.get(f'/research/{research_id}').json()['report'], '')

                rerun = client.post(f'/research/{research_id}/experiments/design-1/rerun')
                self.assertEqual(rerun.status_code, 200, rerun.text)
                self.assertEqual(repo.get(research_id).pending_action, 'rerun_experiment')
                self.assertEqual(client.post(f'/research/{research_id}/resynthesize').status_code, 409)
                with patch('researchpilot.conjecture_pipeline.ConjecturePipeline.execute_experiments') as execute:
                    from researchpilot.worker import JobWorker, research_agent_factory
                    JobWorker(tmp, research_agent_factory(tmp)).run_once(research_id)
                    execute.assert_called_once_with(design_ids={'design-1'})
                rerun_state = repo.get(research_id)
                self.assertEqual(rerun_state.pending_action, '')
                self.assertIsNotNone(rerun_state.assessment)
                self.assertTrue(rerun_state.report)
                self.assertTrue(any(event.action == 'confidence_estimation' for event in rerun_state.trace))

                resynth = client.post(f'/research/{research_id}/resynthesize')
                self.assertEqual(resynth.status_code, 200, resynth.text)
                JobWorker(tmp, research_agent_factory(tmp)).run_once(research_id)
                updated = repo.get(research_id)
                self.assertEqual(updated.pending_action, '')
                self.assertTrue(updated.report)
                self.assertTrue(any(event.action == 'evidence_synthesis' for event in updated.trace))
                self.assertTrue(any(event.action == 'confidence_estimation' for event in updated.trace))

    def test_report_pdf_download_uses_saved_report(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'RESEARCHPILOT_WORKERS': '0'}):
            with TestClient(create_app(tmp)) as client:
                state = client.post('/research', json={'question': 'Is the claim true?'}).json()
                research_id = state['id']
                self.assertEqual(client.get(f'/research/{research_id}/report.pdf').status_code, 409)
                repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
                saved = repo.get(research_id)
                saved.report = '# Assessment\n\nEvidence supports $x^2$.\n'
                repo.save(saved)
                with patch('researchpilot.api.render_latex_pdf', return_value=b'%PDF-1.7\nexample') as render:
                    response = client.get(f'/research/{research_id}/report.pdf')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['content-type'], 'application/pdf')
                self.assertIn('attachment;', response.headers['content-disposition'])
                self.assertEqual(response.content, b'%PDF-1.7\nexample')
                render.assert_called_once_with(saved.report, saved.question)

    def test_cancel_running_investigation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'RESEARCHPILOT_WORKERS': '0'}):
            with TestClient(create_app(tmp)) as client:
                state = client.post('/research', json={'question': 'Measure PCA reconstruction error versus rank.'}).json()
                research_id = state['id']
                self.assertTrue(client.post(f'/research/{research_id}/continue').json()['accepted'])
                queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite')
                job = queue.claim('test-worker', 30)
                self.assertIsNotNone(job)
                repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
                running = repo.get(research_id)
                running.status = Status.RUNNING
                repo.save(running)
                response = client.post(f'/research/{research_id}/cancel')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['status'], 'cancelled')
                self.assertEqual(client.get(f'/research/{research_id}').json()['status'], 'cancelled')
                self.assertEqual(client.get(f'/research/{research_id}/job').json()['status'], 'cancelled')
                self.assertEqual(client.post(f'/research/{research_id}/cancel').status_code, 409)
                self.assertFalse(client.post(f'/research/{research_id}/continue').json()['accepted'])

                queued = client.post('/research', json={'question': 'Measure SVD error versus rank.'}).json()
                self.assertTrue(client.post(f"/research/{queued['id']}/continue").json()['accepted'])
                self.assertEqual(client.post(f"/research/{queued['id']}/cancel").json()['status'], 'cancelled')
                self.assertEqual(queue.get(queued['id']).status, 'cancelled')

    def test_workspace_env_enables_llm_for_attached_paper_run(self):
        calls = []

        def generate(provider, messages, schema):
            payload = __import__("json").loads(messages[1]["content"])
            calls.append(payload)
            properties = schema["properties"]
            if "normalized_statement" in properties:
                return {"normalized_statement": payload["statement"],
                        "mathematical_domain": "numerical linear algebra", "objects": ["Kaczmarz++"],
                        "assumptions": [], "measurable_predictions": ["More iterations under harmonic decay"],
                        "ambiguities": [], "experimentable": True}
            if "queries" in properties:
                return {"queries": ["Kaczmarz++ harmonic spectrum", "Kaczmarz++ flat spectrum",
                                    "accelerated randomized Kaczmarz convergence"], "argument_searches": []}
            if "selected_source_ids" in properties:
                return {"selected_source_ids": [item["source_id"] for item in payload["candidates"]]}
            if "findings" in properties:
                return {"reviews": [{"source_id": item["source_id"], "relevance": "high",
                        "summary": "Defines the method.", "limitations": []}
                        for item in payload["sources"]],
                        "findings": [{"source_id": item["source_id"],
                        "excerpt": item["passages"][0]["text"][:60], "evidence_type": "related_result",
                        "relation": "neutral", "assumptions": [], "notes": "Method definition."}
                        for item in payload["sources"]]}
            if "test_type" in properties:
                return {
                    'name': 'Spectral comparison',
                    'baselines': ['flat spectrum'],
                    'metrics': ['iterations'],
                    'seeds': [0, 1, 2],
                    'algorithm_steps': ['Run paired spectral comparisons.'],
                    'parameter_ranges_json': '{}',
                    'test_type': 'falsifying',
                    'additional_assumptions': [],
                }
            if "code" in properties:
                return {"code": "print('planned')\n"}
            if "summary" in properties:
                return {"summary": "Paper defines the method; experiment awaits execution.",
                        "revised_conjecture": "", "unresolved_questions": []}
            return {"judgment": "Supported in the tested setting; broader generality unresolved", "rationale": "No executed measurements."}

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env.local").write_text(
                "RESEARCHPILOT_PROVIDER=openai\nOPENAI_API_KEY=test-key\nRESEARCHPILOT_STRONG_MODEL=test-model\n"
                "RESEARCHPILOT_WORKERS=0\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), \
                 patch("researchpilot.agent.provider.OpenAIResponsesProvider.generate", autospec=True,
                       side_effect=generate), \
                 patch("researchpilot.agent.provider.OpenAIResponsesProvider.generate_plan", autospec=True,
                       side_effect=generate), \
                 patch("researchpilot.literature.ArxivClient.search", return_value=[]), \
                 patch("researchpilot.literature.CrossrefClient.search", return_value=[]), \
                 TestClient(create_app(tmp)) as client:
                state = client.post("/research", json={"question": (
                    "Accelerated Kaczmarz becomes slower when singular values decay harmonically "
                    "rather than remaining approximately flat.")}).json()
                research_id = state["id"]
                self.assertEqual(state["model"], "openai:test-model")
                uploaded = client.post(f"/research/{research_id}/papers", json={
                    "filename": "method.md", "title": "Kaczmarz++ method",
                    "content_base64": base64.b64encode(
                        b"Algorithm 1 Kaczmarz++ uses adaptive momentum and regularized block projections."
                    ).decode()})
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                completed = client.post(f"/research/{research_id}/continue?background=false").json()
                self.assertGreater(completed["model_calls"], 0)
                self.assertEqual(len(completed["experiments_planned"]), 1)
                self.assertTrue(all("attached_paper_passages" in payload for payload in calls if "normalized_statement" not in payload))

    def test_pdf_context_has_readable_passages(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 20 250 Td (Randomized projections converge quickly.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        buffer = io.BytesIO()
        writer.write(buffer)
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp)) as client:
            state = client.post("/research", json={"question": "When do randomized projections converge?"}).json()
            uploaded = client.post(f"/research/{state['id']}/papers", json={
                "filename": "context.pdf", "title": "Projection context", "content_base64":
                    base64.b64encode(buffer.getvalue()).decode()})
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertGreater(uploaded.json()["chunks"], 0)
            saved = client.get(f"/research/{state['id']}").json()
            self.assertEqual(len(saved["context_source_ids"]), 1)
            self.assertEqual(saved["sources"][0]["title"], "Projection context")

    def test_arxiv_context_is_imported_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper = Path(tmp) / "paper.md"
            paper.write_text("Randomized projections can converge under stated assumptions.", encoding="utf-8")
            with (patch.dict(os.environ, {"RESEARCHPILOT_WORKERS": "0"}),
                  patch("researchpilot.api.PaperDownloader.download", return_value=paper) as download,
                  patch("researchpilot.literature.ArxivClient.search", return_value=[]),
                  patch("researchpilot.api.ArxivClient.lookup", return_value=Source(
                      "arxiv:2401.12345", "The actual paper title", arxiv_id="2401.12345", verified=True)),
                  patch("researchpilot.literature.CrossrefClient.search", return_value=[])):
                with TestClient(create_app(tmp)) as client:
                    state = client.post("/research", json={"question": "When do randomized projections converge?"}).json()
                    research_id = state["id"]
                    invalid = client.post(f"/research/{research_id}/arxiv", json={"url": "https://example.org/pdf/2401.12345"})
                    self.assertEqual(invalid.status_code, 422)
                    imported = client.post(f"/research/{research_id}/arxiv", json={"url": "https://arxiv.org/abs/2401.12345"})
                    self.assertEqual(imported.status_code, 200, imported.text)
                    self.assertGreater(imported.json()["chunks"], 0)
                    saved = client.get(f"/research/{research_id}").json()
                    self.assertEqual(saved["context_source_ids"], ["arxiv:2401.12345"])
                    self.assertEqual(saved["sources"][0]["arxiv_id"], "2401.12345")
                    self.assertEqual(saved["sources"][0]["title"], "The actual paper title")
                    self.assertEqual(client.post(f"/research/{research_id}/arxiv", json={"url": "https://arxiv.org/abs/2401.12345"}).status_code, 409)
                    completed = client.post(f"/research/{research_id}/continue?background=false").json()
                    self.assertIn("arxiv:2401.12345", completed["inspected_papers"])
                    self.assertEqual(completed["sources"][0]["id"], "arxiv:2401.12345")
            download.assert_called_once_with("https://arxiv.org/pdf/2401.12345")

    def test_clarification_feedback_allows_execution(self):
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp)) as client:
            state = client.post("/research", json={"question": "Compare these papers."}).json()
            research_id = state["id"]
            self.assertEqual(state["status"], "needs_input")
            self.assertTrue(state["unresolved_questions"])
            self.assertFalse(client.post(f"/research/{research_id}/continue").json()["accepted"])

            answer = "Compare citation coverage; use papers 10.1000/example1 and 10.1000/example2."
            response = client.post(f"/research/{research_id}/feedback", json={"message": answer})
            self.assertEqual(response.status_code, 200)
            updated = response.json()
            self.assertEqual(updated["status"], "planned")
            self.assertEqual(updated["unresolved_questions"], [])
            self.assertIn(answer, updated["constraints"])
            self.assertTrue(client.post(f"/research/{research_id}/continue").json()["accepted"])

    def test_background_execution_and_trace_views(self):
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp)) as client:
            self.assertEqual(client.get("/health").json()["status"], "ok")
            created = client.post("/research", json={"question": "Plan a scoped research investigation."})
            self.assertEqual(created.status_code, 200)
            state = created.json(); research_id = state["id"]
            self.assertEqual(client.post(f"/research/{research_id}/plan", json={"steps": ["Audit evidence"]}).status_code, 404)
            accepted = client.post(f"/research/{research_id}/continue").json()
            self.assertTrue(accepted["accepted"])
            for _ in range(50):
                state = client.get(f"/research/{research_id}").json()
                if state["status"] == "completed": break
                time.sleep(.02)
            self.assertEqual(state["status"], "completed")
            self.assertTrue(client.get(f"/research/{research_id}/trace").json())
            self.assertEqual(client.get(f"/research/{research_id}/job").json()["status"], "completed")
            self.assertTrue((Path(tmp) / "traces" / f"{research_id}.trace.json").exists())
            saved = client.get("/research").json()[0]
            self.assertEqual(saved["id"], research_id)
            self.assertEqual(saved["question"], "Plan a scoped research investigation.")
            self.assertEqual(client.get("/deployment/quota").json(), {"enabled": False, "mode": "Full", "experiments_allowed": True})

    def test_validation_and_missing_resources(self):
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp)) as client:
            self.assertEqual(client.post("/research", json={"question": "x"}).status_code, 422)
            self.assertEqual(client.get("/research/missing").status_code, 404)
            self.assertEqual(client.get("/research/missing/artifact/0").status_code, 404)

    def test_cors_for_frontend_origin(self):
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp)) as client:
            response = client.options("/research", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
            self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:3000")

    def test_user_paper_ingestion_and_retrieval(self):
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(tmp)) as client:
            state = client.post("/research", json={"question": "Find research about randomized projections."}).json()
            content = b"# Theorem\nRandomized projections have expected exponential convergence under a scaled condition bound."
            upload = client.post(f"/research/{state['id']}/papers", json={"filename": "paper.md",
                "content_base64": base64.b64encode(content).decode(), "title": "Uploaded Projection Note",
                "authors": ["A. Researcher"], "doi": "10.1/example"})
            self.assertEqual(upload.status_code, 200)
            self.assertGreater(upload.json()["chunks"], 0)
            retrieved = client.get(f"/research/{state['id']}/retrieval", params={"q": "exponential convergence"}).json()
            self.assertEqual(retrieved[0]["chunk"]["source_id"], "doi:10.1/example")

    def test_optional_bearer_auth(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RESEARCHPILOT_API_TOKEN": "test-secret"}):
            with TestClient(create_app(tmp)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/research").status_code, 401)
                response = client.get("/research", headers={"Authorization": "Bearer test-secret"})
                self.assertEqual(response.status_code, 200)
