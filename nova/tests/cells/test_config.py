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
Unit tests for cell config/permissions.
"""

from nova.cells import config
from nova import context
from nova import flags
from nova.openstack.common import log as logging
from nova import test

FLAGS = flags.FLAGS
LOG = logging.getLogger(__name__)


class MockCellsConfig(config.CellsConfig):
    def _create_config(self, config=None):

        if not config:
            cell_1_rules = (
                ('somerole', 'allow', 'b'),
                ('somerole', 'deny', 'b'),
                ('reader', 'allow', 'r'),
            )
            cell_2_rules = (
                ('bofh', 'deny', 'b'),
                ('otherrole', 'allow', 'b'),
            )

            config = {
                'c1': {'rules': cell_1_rules},
                'c2': {'rules': cell_2_rules, 'read_only': True},
            }

        return config

    def _reload_cells_config(self):
        self._cells_config = self._create_config()


class ConfigTest(test.TestCase):
    """Test cell configuration loading, modification, and permissions tests."""

    def setUp(self):
        super(ConfigTest, self).setUp()

        self.cfg = MockCellsConfig()
        self.context = context.RequestContext('fake', 'fake')

    def test_config_get_cell(self):
        cell_2 = self.cfg.get_cell_dict('c2')
        self.assertTrue("rules" in cell_2)

    def test_config_get_value(self):
        rules = self.cfg.get_value('c2', 'rules')
        expected = ('bofh', 'deny', 'b')
        self.assertEqual(expected, rules[0])

    def test_config_get_value_default(self):
        self.assertEqual('def', self.cfg.get_value('c2', 'key', 'def'))

    def test_default_permission_cell_star(self):
        self.context.roles = ['cell-*']
        allowed = self.cfg.cell_has_permission('c2', self.context, 'b')
        self.assertTrue(allowed)

    def test_default_permission_cell_single(self):
        self.context.roles = ['cell-c1']
        allowed = self.cfg.cell_has_permission('c1', self.context, 'b')
        self.assertTrue(allowed)

    def test_default_permission_cell_bofh(self):
        self.context.roles = ['bofh']
        allowed = self.cfg.cell_has_permission('c1', self.context, 'b')
        self.assertTrue(allowed)

    def test_override_short_circuit(self):
        """First matching rule wins"""
        self.context.roles = ['somerole']
        allowed = self.cfg.cell_has_permission('c1', self.context, 'b')
        self.assertTrue(allowed)

    def test_override_deny(self):
        self.context.roles = ['bofh']
        allowed = self.cfg.cell_has_permission('c2', self.context, 'b')
        self.assertFalse(allowed)

    def test_rule_list(self):
        """Test that each rule in the list is applied"""
        self.context.roles = ['otherrole']
        allowed = self.cfg.cell_has_permission('c2', self.context, 'b')
        self.assertTrue(allowed)

    def test_cell_read_only_all(self):
        """Test setting entire cell read-only, regardless of user role"""
        self.context.roles = ['otherrole']
        read_only = self.cfg.cell_read_only('c2', self.context)
        self.assertTrue(read_only)

    def test_cell_read_only_by_role(self):
        self.context.roles = ['reader']
        read_only = self.cfg.cell_read_only('c1', self.context)
        self.assertTrue(read_only)
