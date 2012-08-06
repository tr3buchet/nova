import mock

from nova.api.openstack.compute.contrib import services
from nova import test
from nova.tests.api.openstack import fakes


class TestCellsServicesController(test.TestCase):

    non_compute_service = {
        'disabled': False,
        'updated_at': None,
        'report_count': 123,
        'topic': 'not_compute',
        'host': 'non_compute_host',
        'href': 'http://test/v1/services/c0001-1',
        'id': "1",
    }

    compute_service = {
        'topic': 'compute',
        'host': 'compute_host',
        'compute_node': [{
            'vcpus': 1,
            'memory_mb': 1024,
            'local_gb': 10,
            'vcpus_used': 1,
            'memory_mb_used': 123,
            'local_gb_used': 4,
            'cpu_info': '',
            'hypervisor_type': 'xen',
            'hypervisor_version': 6,
            'hypervisor_hostname': 'hypervisor_host',
        }],
    }

    def setUp(self):
        """
        Run before each test.
        """
        super(TestCellsServicesController, self).setUp()
        # Fake/Mock Nova Context
        self.fake_context = mock.Mock()
        self.fake_context.read_deleted = "no"
        self.fake_context.project_id = "fake_project"

        # Fake/Mock WSGI Request
        self.fake_req = mock.MagicMock()
        self.fake_req.environ = {"nova.context": self.fake_context}
        self.fake_req.application_url = "http://test/v1"

        # Mock/Patch the cell_broadcast_call method
        bc_patch = mock.patch("nova.cells.api.cell_broadcast_call")
        self.bc_mock = bc_patch.start()

        # Mock/Patch db.instance_get_all_by_host
        db_get_all_patch = mock.patch('nova.db.instance_get_all_by_host')
        self.db_get_all_mock = db_get_all_patch.start()

        def _cleanup_func():
            bc_patch.stop()
            db_get_all_patch.stop()

        self._stop_func = _cleanup_func

        # Create controller to be used in each test
        self.controller = services.CellsServicesController()

    def tearDown(self):
        self._stop_func()
        super(TestCellsServicesController, self).tearDown()

    def test_empty_index(self):
        """
        Test showing empty index of services.
        """
        self.bc_mock.return_value = [([], "c0001")]
        response = self.controller.index(self.fake_req)
        self.assertEqual({"services": []}, response)

    def test_index(self):
        """
        Test showing index of services.
        """
        self.bc_mock.return_value = [([self.non_compute_service], "c0001")]
        response = self.controller.index(self.fake_req)
        self.assertEqual({"services": [self.non_compute_service]}, response)

    def test_show(self):
        """
        Test showing a single service.
        """
        self.bc_mock.return_value = [(self.non_compute_service, "c0001")]
        response = self.controller.show(self.fake_req, "c0001-1")
        self.assertEqual({"service": self.non_compute_service}, response)

    def test_details_non_compute(self):
        """
        Test retrieving details on a non compute-type service.
        """
        self.bc_mock.return_value = [(self.non_compute_service, "c0001")]
        response = self.controller.details(self.fake_req, "c0001-1")
        self.assertEqual({"details": {}}, response)

    def test_details_compute(self):
        """
        Test retrieving details on a compute-type service.
        """
        self.bc_mock.return_value = [(self.compute_service, "c0001")]
        response = self.controller.details(self.fake_req, "c0001-1")

        expected = {
            'details': {
                'vcpus': 1,
                'memory_mb': 1024,
                'local_gb': 10,
                'vcpus_used': 1,
                'memory_mb_used': 123,
                'memory_mb_used_servers': 0,
                'local_gb_used': 4,
                'cpu_info': '',
                'hypervisor_type': 'xen',
                'hypervisor_version': 6,
                'hypervisor_hostname': 'hypervisor_host',
            }
        }
        self.assertEqual(expected, response)

    def test_no_servers_for_service(self):
        """
        Test retrieving an empty list of servers on a compute node.
        """
        self.bc_mock.return_value = [(self.compute_service, "c0001")]
        response = self.controller.servers(self.fake_req, "c0001-1")
        self.assertEqual({"servers": []}, response)

    def test_servers_for_service(self):
        """
        Test retrieving a list of servers on a compute node.
        """
        self.bc_mock.return_value = [(self.compute_service, "c0001")]
        self.db_get_all_mock.return_value = [
            fakes.fake_instance_get()(self.fake_context, "fake_uuid1"),
            fakes.fake_instance_get()(self.fake_context, "fake_uuid2"),
        ]
        response = self.controller.servers(self.fake_req, "c0001-1")
        self.assertEqual(2, len(response["servers"]))
