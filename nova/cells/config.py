# Copyright (c) 2012 Openstack, LLC.
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

"""Cells Configuration"""

from nova import flags
from nova.openstack.common import cfg
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova import utils

flag_opts = [
        cfg.StrOpt('cells_config_file',
                   default=None,
                   help="Configuration file for cells")
]

FLAGS = flags.FLAGS
FLAGS.register_opts(flag_opts)
LOG = logging.getLogger('nova.cells.config')


class CellsConfig(object):
    def __init__(self):
        cells_config_file = FLAGS.cells_config_file
        if cells_config_file:
            self._cells_config_file = FLAGS.find_file(cells_config_file)
        else:
            self._cells_config_file = None
        self._cells_config_cacheinfo = {}
        self._cells_config = {}
        self._reload_cells_config()

    def _reload_cells_config(self):
        def _reload(data):
            self._cells_config = jsonutils.loads(data)

        if not self._cells_config_file:
            cells_config_file = FLAGS.cells_config_file
            if cells_config_file:
                # See if it exists now
                self._cells_config_file = FLAGS.find_file(cells_config_file)

        if self._cells_config_file:
            utils.read_cached_file(self._cells_config_file,
                    self._cells_config_cacheinfo, reload_func=_reload)

    def get_cell_dict(self, cell_name):
        self._reload_cells_config()
        return self._cells_config.get(cell_name, {})

    def get_value(self, cell_name, key, default=None):
        cell_info = self.get_cell_dict(cell_name)
        return cell_info.get(key, default)

    def cell_has_permission(self, cell_name, context, permission):
        roles = set(context.roles)
        default_allowed_roles = set(['cell-*', 'cell-%s' % cell_name])
        # FIXME(comstud): Temporary hack until we set 'cell-*' on the
        # roles that we want to be able to build in all cells, etc.
        default_allowed_roles.add('bofh')
        # Default
        allowed = len(roles & default_allowed_roles) > 0

        # Run override rules:
        rules = self.get_value(cell_name, 'rules')
        if rules is None:
            # no overrides, use default rules
            return allowed

        for rule in rules:
            override = self._run_rule(context, rule, permission)
            if override is not None:
                # short-circuit rule matching
                return override

        # no rule was specified matching any of the user's roles
        # fall back to default
        return allowed

    def _run_rule(self, context, rule, permission):
        """ Test the given rule for the requested permission

        :params context: security context
        :rule: 3-tuple of (role, action, permissions)
        :params action: Either 'allow' or 'deny'
        :permission: single character permission to check
        :returns: True if permission is allowed, False if denied, or None if
                  the rule doesn't apply to the user's roles
        """
        role, action, perm_chars = rule
        action = action.lower()

        if role not in context.roles:
            return None

        # Found a matching role override
        if '*' in perm_chars:
            return "allow" == action

        if permission in perm_chars:
            return "allow" == action

        return None

    def cell_read_only(self, cell_name, context):
        ro = self.get_value(cell_name, 'read_only', False)
        if ro or context is None:
            return ro
        return self.cell_has_permission(cell_name, context, 'r')
