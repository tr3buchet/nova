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
Rackspace Near/Far filter.
"""

from nova.cells import filters
from nova.cells import utils as cells_utils
from nova import db
from nova import exception
from nova.openstack.common import log as logging

LOG = logging.getLogger(__name__)


class RAXNearFarCellFilter(filters.BaseCellFilter):
    """Rackspace near/far instance filter.
    Check for 'near' or 'far' in the scheduler_hints dict.  Values
    are instance_uuids.

    'near' an instance_uuid needs to target the build for the same
    cell as instance_uuid.

    'far' means to target the build for a different cell than
    instance_uuid.
    """

    @staticmethod
    def _get_cell_name(context, instance_uuid, filter_type):
        try:
            instance = db.instance_get_by_uuid(context,
                    instance_uuid)
        except exception.InstanceNotFound:
            reason = _("Instance '%(instance_uuid)s' not found for "
                    "'%(filter_type)s' scheduler_hint") % locals()
            raise exception.CellsFilterError(reason=reason)
        cell_name = instance['cell_name']
        if not cell_name:
            reason = _("Instance '%(instance_uuid)s' is not assigned to a "
                    "cell for '%(filter_type)s' scheduler_hint") % locals()
            raise exception.CellsFilterError(reason=reason)
        return cell_name

    @staticmethod
    def _find_cell(cell_name, cells_manager, routing_path):
        try:
            (next_hop_name, host) = cells_utils.cell_name_for_next_hop(
                    cell_name, routing_path)
        except exception.CellRoutingInconsistency:
            return None
        if not next_hop_name:
            return cells_manager.my_cell_info
        return cells_manager.child_cells.get(next_hop_name)

    def filter_cells(self, cells, filter_properties):
        context = filter_properties['context']
        scheduler_hints = filter_properties.get('scheduler_hints')
        if not scheduler_hints:
            return
        routing_path = filter_properties['routing_path']
        cells_manager = filter_properties['scheduler'].manager

        # First, we need to turn 'near' and 'far' into 'same_cell'
        # and 'different_cell' hints.  When we're routing down,
        # we may have some routing-only hops, so they will not be
        # able to look up instances.
        near_uuid = scheduler_hints.pop('near', None)
        if near_uuid:
            cell_name = self._get_cell_name(context, near_uuid, 'near')
            scheduler_hints['same_cell'] = cell_name
        far_uuid = scheduler_hints.pop('far', None)
        if far_uuid:
            cell_name = self._get_cell_name(context, far_uuid, 'far')
            scheduler_hints['different_cell'] = cell_name
            cell = self._find_cell(cell_name,
                    cells_manager, routing_path)
            if cell:
                # We should also try to filter DCZONE if we have it.
                dczone = cell.capabilities.get('DCZONE')
                if dczone:
                    scheduler_hints['different_dczone'] = dczone[0]

        # Now we can look at 'same_cell', 'different_cell', and
        # 'different_dczone'
        same_cell = scheduler_hints.get('same_cell')
        if same_cell:
            LOG.info(_("Forcing direct route to %(same_cell)s because "
                    "of 'same_cell' scheduler hint"), locals())
            return {'action': 'direct_route',
                    'target': same_cell}
        different_cell = scheduler_hints.get('different_cell')
        if different_cell:
            hops_left = (different_cell.count(
                    cells_utils.PATH_CELL_SEP) - routing_path.count(
                            cells_utils.PATH_CELL_SEP))
            cell = self._find_cell(different_cell,
                    cells_manager, routing_path)
            # If there's only 1 hop left, we need to remove
            # this cell.. otherwise it's okay to include it,
            # because the next cell down can filter.
            #
            # Also, if we're the cell, remove ourselves from
            # the set.  This should be the case if hops_left == 0
            if cell and hops_left <= 1:
                try:
                    cell_name = cell.name
                    LOG.info(_("Removing cell %(cell)s because "
                            "of 'different_cell' scheduler_hint of "
                            "'%(different_cell)s'"), locals())
                    cells.remove(cell)
                except KeyError:
                    pass
                if not cells:
                    return

        different_dczone = scheduler_hints.get('different_dczone')
        if different_dczone:
            matching_dczone_cells = [cell for cell in cells
                    if cell.capabilities.get('DCZONE', [None])[0] ==
                            different_dczone]
            # Remove cells that match the DCZONE
            if matching_dczone_cells:
                LOG.info(_("Removing cells %(matching_dczone_cells)s "
                        "because of 'different_dczone' scheduler_hint "
                        "of %(different_dczone)s"), locals())
                return {'drop': matching_dczone_cells}
