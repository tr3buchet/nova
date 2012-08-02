"""
Rackspace target_cell filter.
"""

from nova.cells import filters
from nova.openstack.common import log as logging

LOG = logging.getLogger(__name__)


class RAXTargetCellFilter(filters.BaseCellFilter):
    def filter_cells(self, cells, filter_properties):
        scheduler_hints = filter_properties.get('scheduler_hints')
        if not scheduler_hints:
            return
        cell_name = scheduler_hints.pop('target_cell', None)
        if not cell_name:
            return

        LOG.info(_("Forcing direct route to %(cell_name)s because "
                "of 'target_cell' scheduler hint"), locals())
        return {'action': 'direct_route',
                'target': cell_name}
