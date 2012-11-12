# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Piston Cloud Computing, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
Tests For Compute w/ Cells
"""
import functools

from nova.compute import cells_api as compute_cells_api
from nova import db
from nova import flags
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova.tests.compute import test_compute


LOG = logging.getLogger('nova.tests.test_compute_cells')
FLAGS = flags.FLAGS

ORIG_COMPUTE_API = None


def stub_call_to_cells(context, instance, method, *args, **kwargs):
    fn = getattr(ORIG_COMPUTE_API, method)
    original_instance = kwargs.pop('original_instance', None)
    if original_instance:
        instance = original_instance
        # Restore this in child cell DB
        db.instance_update(context, instance['uuid'],
                           dict(vm_state=instance['vm_state'],
                                task_state=instance['task_state']))
    return fn(context, instance, *args, **kwargs)


def stub_cast_to_cells(context, instance, method, *args, **kwargs):
    fn = getattr(ORIG_COMPUTE_API, method)
    original_instance = kwargs.pop('original_instance', None)
    if original_instance:
        instance = original_instance
        # Restore this in child cell DB
        db.instance_update(context, instance['uuid'],
                           dict(vm_state=instance['vm_state'],
                                task_state=instance['task_state']))
    fn(context, instance, *args, **kwargs)


def deploy_stubs(stubs, api, original_instance=None):
    call = stub_call_to_cells
    cast = stub_cast_to_cells

    if original_instance:
        kwargs = dict(original_instance=original_instance)
        call = functools.partial(stub_call_to_cells, **kwargs)
        cast = functools.partial(stub_cast_to_cells, **kwargs)

    stubs.Set(api, '_call_to_cells', call)
    stubs.Set(api, '_cast_to_cells', cast)


def wrap_create_instance(func):

    @functools.wraps(func)
    def wraper(self, *args, **kwargs):
        instance = self._create_fake_instance()

        def fake(*args, **kwargs):
            return instance

        self.stubs.Set(self, '_create_fake_instance', fake)
        original_instance = jsonutils.to_primitive(instance)
        deploy_stubs(self.stubs, self.compute_api,
                     original_instance=original_instance)
        return func(self, *args, **kwargs)

    return wraper


class CellsComputeAPITestCase(test_compute.ComputeAPITestCase):
    def setUp(self):
        super(CellsComputeAPITestCase, self).setUp()
        global ORIG_COMPUTE_API
        ORIG_COMPUTE_API = self.compute_api

        def _fake_cell_read_only(*args, **kwargs):
            return False

        def _fake_validate_cell(*args, **kwargs):
            return

        def _nop_update(context, instance, **kwargs):
            return instance

        self.compute_api = compute_cells_api.ComputeCellsAPI()
        self.stubs.Set(self.compute_api, '_cell_read_only',
                       _fake_cell_read_only)
        self.stubs.Set(self.compute_api, '_validate_cell',
                       _fake_validate_cell)

        # NOTE(belliott) Don't update the instance state
        # for the tests at the API layer.  Let it happen after
        # the stub cast to cells so that expected_task_states
        # match.
        self.stubs.Set(self.compute_api, 'update', _nop_update)

        deploy_stubs(self.stubs, self.compute_api)

    def tearDown(self):
        global ORIG_COMPUTE_API
        self.compute_api = ORIG_COMPUTE_API
        super(CellsComputeAPITestCase, self).tearDown()

    # NOTE(jkoelker) These tests require that we don't "share"
    #                the db between compute and cells. We fake this out
    #                by copying the instance created and setting the
    #                cells partial to supply the "original" instance
    #                to the child cell
    @wrap_create_instance
    def test_backup(self):
        return super(CellsComputeAPITestCase, self).test_backup()

    @wrap_create_instance
    def test_snapshot(self):
        return super(CellsComputeAPITestCase, self).test_snapshot()

    @wrap_create_instance
    def test_snapshot_image_metadata_inheritance(self):
        return super(CellsComputeAPITestCase,
                     self).test_snapshot_image_metadata_inheritance()

    @wrap_create_instance
    def test_snapshot_minram_mindisk(self):
        return super(CellsComputeAPITestCase,
                     self).test_snapshot_minram_mindisk()

    @wrap_create_instance
    def test_snapshot_minram_mindisk_VHD(self):
        return super(CellsComputeAPITestCase,
                     self).test_snapshot_minram_mindisk_VHD()

    @wrap_create_instance
    def test_snapshot_minram_mindisk_img_missing_minram(self):
        return super(CellsComputeAPITestCase,
                     self).test_snapshot_minram_mindisk_img_missing_minram()

    @wrap_create_instance
    def test_snapshot_minram_mindisk_no_image(self):
        return super(CellsComputeAPITestCase,
                     self).test_snapshot_minram_mindisk_no_image()

    @wrap_create_instance
    def test_snapshot_given_image_uuid(self):
        return super(CellsComputeAPITestCase,
                     self).test_snapshot_given_image_uuid()

    def test_instance_metadata(self):
        # Overriding this because the test doesn't work with how
        # we test cells.
        pass

    def test_live_migrate(self):
        # Overriding this because the test doesn't work with how
        # we test cells.
        pass


class CellsComputePolicyTestCase(test_compute.ComputePolicyTestCase):
    def setUp(self):
        super(CellsComputePolicyTestCase, self).setUp()
        global ORIG_COMPUTE_API
        ORIG_COMPUTE_API = self.compute_api
        self.compute_api = compute_cells_api.ComputeCellsAPI()
        deploy_stubs(self.stubs, self.compute_api)

    def tearDown(self):
        global ORIG_COMPUTE_API
        self.compute_api = ORIG_COMPUTE_API
        super(CellsComputePolicyTestCase, self).tearDown()
