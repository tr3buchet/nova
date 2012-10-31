# Copyright (c) 2012 Openstack, LLC
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
Unit tests for cell permissions filter.
"""

from nova.cells import config
from nova.cells.filters import permissions
from nova.cells import manager
from nova.cells import utils
from nova import context
from nova.openstack.common import log as logging
from nova import test

LOG = logging.getLogger(__name__)

CELL_NAMES = ["c1", "c2", "c3"]


class MockConfig(config.CellsConfig):
    def _reload_cells_config(self):

        c1_full_name = utils.PATH_CELL_SEP.join(["US", "East",
            CELL_NAMES[0]])
        cell1_rules = [
            ('somerole', 'allow', 'b'),
        ]

        c2_full_name = utils.PATH_CELL_SEP.join(["US", "East",
            CELL_NAMES[1]])
        cell2_rules = [
            ('somerole', 'deny', 'b'),
        ]

        c3_full_name = utils.PATH_CELL_SEP.join(["US", "East",
            CELL_NAMES[2]])
        cell3_rules = [
            ('bofh', 'deny', 'b'),
            ('otherrole', 'allow', 'b'),
            ('somerole', 'allow', 'b'),
        ]

        config = {
            c1_full_name: {'rules': cell1_rules},
            c2_full_name: {'rules': cell2_rules},
            c3_full_name: {'rules': cell3_rules},
        }
        self._cells_config = config


class PermissionsFilterTest(test.TestCase):
    """Test use of cell build permissions to filter list of cells"""

    def setUp(self):
        super(PermissionsFilterTest, self).setUp()

        my_cell_name = utils.PATH_CELL_SEP.join(["US", "East", "c2"])
        self.flags(cell_name=my_cell_name, host='zehost')

        self.config = MockConfig()
        self.pfilter = permissions.CellPermissionsFilter()

        self.cells = [manager.CellInfo(name) for name in CELL_NAMES]
        self.ctxt = context.RequestContext('fake', 'fake', roles=['bofh'])

        routing_path = utils.PATH_CELL_SEP.join(
                map(utils.PATH_CELL_HOST_SEP.join,
                    [("US", "bigdaddyhost"), ("East", "eastcoasthost")]))

        self.filter_properties = {'routing_path': routing_path,
                                  'context': self.ctxt,
                                  'cells_config': self.config}

    def testDefaultRole(self):
        drop = self.pfilter.filter_cells(self.cells,
                self.filter_properties)
        drop_cells = drop['drop']
        self.assertEqual(1, len(drop_cells))
        # c3 is explicitly denied to 'bofh'
        self.assertEqual("c3", drop_cells[0].name)

    def testDefaultAllowAll(self):
        self.ctxt.roles = ['cell-*']
        drop = self.pfilter.filter_cells(self.cells,
                self.filter_properties)
        drop_cells = drop['drop']
        self.assertEqual(0, len(drop_cells))

    def testDefaultSingleCell(self):
        c1_full_name = utils.PATH_CELL_SEP.join(["US", "East", CELL_NAMES[0]])
        self.ctxt.roles = ['cell-%s' % c1_full_name]
        drop = self.pfilter.filter_cells(self.cells,
                self.filter_properties)
        drop_cells = drop['drop']
        self.assertEqual(2, len(drop_cells))

    def testOverride(self):
        self.ctxt.roles = ['somerole']
        drop = self.pfilter.filter_cells(self.cells,
                self.filter_properties)
        drop_cells = drop['drop']
        self.assertEqual(1, len(drop_cells))
        self.assertEqual("c2", drop_cells[0].name)
