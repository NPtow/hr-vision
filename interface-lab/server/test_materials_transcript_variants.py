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

if __name__=='__main__':
    unittest.main()
