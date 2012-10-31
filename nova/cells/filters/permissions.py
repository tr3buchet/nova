# Copyright (c) 2011-2012 OpenStack, LLC.
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
Cell Permissions Filter

Checks permissions in config file to see which cells should be filtered
"""

from nova.cells import filters
from nova.cells import utils as cells_utils
from nova.openstack.common import log as logging

LOG = logging.getLogger(__name__)


class CellPermissionsFilter(filters.BaseCellFilter):

    def filter_cells(self, cells, filter_properties):
        context = filter_properties['context']
        routing_path = filter_properties['routing_path']
        current_cell = cells_utils.path_without_hosts(routing_path)
        cells_config = filter_properties['cells_config']
        drop_cells = []
        for cell in cells:
            full_cell_name = current_cell
            if not cell.is_me:
                full_cell_name += cells_utils.PATH_CELL_SEP + cell.name
            if cells_config.cell_has_permission(full_cell_name,
                    context, 'b'):
                continue
            drop_cells.append(cell)
        if drop_cells:
            drop_msg = [str(cell) for cell in drop_cells]
            LOG.info(_("Dropping cells '%(drop_msg)s' due to lack of "
                    "permissions"), locals())
        return {'drop': drop_cells}
