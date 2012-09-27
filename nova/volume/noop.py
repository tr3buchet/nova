import nova.volume.cinder

class NoopVolumeApi(nova.volume.cinder.API):

    def __init__(self, *args, **kwargs):
        super(NoopVolumeApi, self).__init__(*args, **kwargs)

    def __getattribute__(self, name):
        if name == 'get':
            return super(NoopVolumeApi, self).get
        return lambda *args, **kwargs: None
