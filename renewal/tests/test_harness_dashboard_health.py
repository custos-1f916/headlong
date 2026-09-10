"""A dashboard that serves its shell but loses the identity must fail probation."""
import io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.guest_agent import dashboard_health
class Reply(io.BytesIO):
 status=200
class DashboardHealth(unittest.TestCase):
 def replies(self,identities):
  return [Reply(b'<html>SPA shell</html>'),Reply(json.dumps(identities).encode()),Reply(b'{"live":true}')]
 def row(self,running=True):return {'id':'.identities~custos','root_trajectory':'7ed4c8d8-4fc3-43b7-a812-3c29d92fbb1d','dispatcher':{'running':running}}
 def test_http_200_with_empty_identity_scan_fails(self):
  with patch('harness.guest_agent.urllib.request.urlopen',side_effect=self.replies([])):
   with self.assertRaisesRegex(RuntimeError,'cannot discover'):dashboard_health()
 def test_discovered_but_stopped_identity_fails(self):
  with patch('harness.guest_agent.urllib.request.urlopen',side_effect=self.replies([self.row(False)])):
   with self.assertRaisesRegex(RuntimeError,'cannot discover'):dashboard_health()
 def test_same_display_id_with_wrong_root_trajectory_fails(self):
  row=self.row();row['root_trajectory']='wrong'
  with patch('harness.guest_agent.urllib.request.urlopen',side_effect=self.replies([row])):
   with self.assertRaisesRegex(RuntimeError,'cannot discover'):dashboard_health()
 def test_page_identity_and_status_healthy(self):
  with patch('harness.guest_agent.urllib.request.urlopen',side_effect=self.replies([self.row()])):
   self.assertTrue(dashboard_health()['status_api'])
