"""ASR alternatives must not change the capture transcript used by assessment."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from core_source_client import CoreMaterialsCatalog
from materials_service import MaterialsService


def asset(identity, external, *, core):
    return {'id':identity,'kind':'transcript','state':'ready','metadata':{},
            ('external_id' if core else 'provider_asset_id'):external}


def version(identity, asset_id, text, *, revision=1, origin='zoom_provider', model=None):
    return {'id':identity,'asset_id':asset_id,'version':revision,
        'segments':[{'text':text,'start_ms':0,'end_ms':1000,'speaker':None}],
        'metadata':{'origin':origin,'model_version':model,
            'normalized':{'full_text':text,'vtt':'','warnings':[]}}}


class TranscriptVariantProjectionTests(unittest.TestCase):
    def project(self, assets, versions, *, core):
        data={'sources':[],'assets':assets,'transcripts':versions,'jobs':[],
              'channels':[{'video_status':'not_recorded','transcript_status':'ready'}]}
        if core:
            api=Mock()
            api.materials.return_value=data
            return CoreMaterialsCatalog(api,connect=Mock()).legacy_materials('interview',{'id':'owner','room':{'provider':'zoom'}})
        runtime=SimpleNamespace(repository=Mock(),clock=lambda:0,settle_seconds=120)
        service=MaterialsService(runtime)
        service.list_materials=Mock(return_value=data)
        return service.legacy_materials('interview',{'id':'owner','room':{'provider':'zoom'}})

    def test_single_capture_without_variants_has_unchanged_shape_and_text(self):
        for core in (True,False):
            with self.subTest(core=core):
                result=self.project([asset('capture','zoom-vtt',core=core)],
                    [version('original','capture','Original Zoom words')],core=core)
                self.assertEqual(len(result['transcripts']),1)
                self.assertEqual(result['transcripts'][0],{'id':'original','assetId':'capture','revision':1,
                    'text':'Original Zoom words','fullText':'Original Zoom words',
                    'segments':[{'text':'Original Zoom words','start_ms':0,'end_ms':1000,'speaker':None}],
                    'warnings':[],'storage':'owned'})
                self.assertNotIn('transcriptVariants',result)
                self.assertEqual(result['transcription']['ids'],['original'])

    def test_original_zoom_remains_default_with_multiple_provider_neutral_variants(self):
        for core in (True,False):
            with self.subTest(core=core):
                assets=[asset('capture','zoom-vtt',core=core),
                    asset('asr-one','asr:deepgram:recording:job:revision',core=core),
                    asset('asr-two','asr:future_provider:recording:job:revision',core=core)]
                versions=[version('original','capture','Original Zoom words'),
                    version('alternative-one','asr-one','ASR words',origin='deepgram',model='nova-3'),
                    version('alternative-two','asr-two','Other ASR words',origin='future_provider',model='model-v2')]
                before=copy.deepcopy((assets,versions))
                result=self.project(assets,versions,core=core)
                self.assertEqual([v['id'] for v in result['transcripts']],['original'])
                self.assertEqual(result['transcripts'][0]['text'],'Original Zoom words')
                self.assertEqual(result['transcription']['ids'],['original'])
                self.assertEqual([v['id'] for v in result['transcriptVariants']],['alternative-one','alternative-two'])
                self.assertEqual(result['transcriptVariants'][0]['origin'],'deepgram')
                self.assertEqual(result['transcriptVariants'][0]['model'],'nova-3')
                for projected in result['transcriptVariants']:
                    self.assertTrue({'id','assetId','revision','text','fullText','segments','warnings','storage'} <= projected.keys())
                self.assertEqual((assets,versions),before)

    def test_variant_alone_does_not_become_default_and_latest_per_asset_is_preserved(self):
        for core in (True,False):
            with self.subTest(core=core):
                result=self.project([asset('asr','asr:provider:recording:job:revision',core=core)],
                    [version('old','asr','Old ASR',origin='provider'),
                     version('latest','asr','Latest ASR',revision=2,origin='provider')],core=core)
                self.assertEqual(result['transcripts'],[])
                self.assertEqual(result['transcription']['ids'],[])
                self.assertEqual([v['id'] for v in result['transcriptVariants']],['latest'])
                self.assertEqual(result['transcriptVariants'][0]['revision'],2)

class PrimaryTranscriptProjectionTests(unittest.TestCase):
    def data(self, status='ready', tid='chosen'):
        return {'sources':[{'id':'binding','generation':1,'revoked':False}],
            'assets':[asset('capture','zoom-vtt',core=True),asset('asr','asr:any',core=True)],
            'transcripts':[version('original','capture','Zoom words'),
                version('chosen','asr','Chosen words',origin='replaceable-asr'),
                version('later','asr','Later unselected words',revision=2,origin='replaceable-asr')],
            'transcript_selection_required_recording_ids':['video'],
            'transcript_selections':[{'binding_id':'binding','generation':1,'recording_id':'video',
                'request_id':'job','revision':1,'status':status,'transcript_id':tid}],
            'channels':[{'video_status':'not_recorded','transcript_status':'ready'}]}

    def project(self, data, current=None):
        api=Mock()
        api.materials.side_effect=[data,current if current is not None else data]
        return CoreMaterialsCatalog(api,connect=Mock()).legacy_materials('interview',{'id':'owner','room':{'provider':'zoom'}})

    def test_explicit_selection_is_exact_version_not_latest_or_provider_name(self):
        result=self.project(self.data())
        self.assertEqual([t['id'] for t in result['transcripts']],['chosen'])
        self.assertEqual(result['transcripts'][0]['text'],'Chosen words')
        self.assertEqual(result['transcripts'][0]['recordingId'],'video')
        self.assertEqual(result['transcription']['status'],'ready')
        self.assertEqual({t['id'] for t in result['transcriptVariants']},{'original','later'})

    def test_pending_failed_missing_and_stale_never_fall_back_to_zoom(self):
        for status,tid,expected in [('pending',None,'processing'),('failed',None,'error'),
                                    ('uncertain',None,'error'),('ready','missing','error')]:
            with self.subTest(status=status):
                result=self.project(self.data(status,tid))
                self.assertEqual(result['transcripts'],[])
                self.assertEqual(result['transcription']['status'],expected)
        data=self.data();data['transcript_selections']=[]
        self.assertEqual(self.project(data)['transcripts'],[])
        self.assertEqual(self.project(data)['transcription']['status'],'processing')

    def test_same_generation_selection_change_during_access_is_rejected(self):
        from domain import Problem
        data=self.data();current=copy.deepcopy(data)
        current['transcript_selections'][0].update(request_id='new-job',revision=2,status='pending',transcript_id=None)
        with self.assertRaises(Problem) as raised:self.project(data,current)
        self.assertEqual(raised.exception.status,409)

if __name__=='__main__':
    unittest.main()
