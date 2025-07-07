import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Button, Card, FormTextarea, Track, Dialog } from 'components';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import './Editor.scss';

interface KnowledgeBaseDetailData {
  id: string;
  agency: string;
  domain: string;
  url: string;
  cleanedData: string;
  lastUpdate: string;
  originallyScraped: string;
}

interface KnowledgeBaseUpdateData {
  cleanedData: string;
}
type EditorState = 'raw' | 'cleaned' | 'edited' | null;

interface EditorProps {
  editorState: EditorState;
  changeEditorState: (state: EditorState) => void;
  onCancel: () => void;
  onSave: () => void;
}

const KnowledgeBaseDetail: FC<EditorProps> = ({
  editorState,
  changeEditorState,
  onCancel,
  onSave,
}) => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();

  const [formData, setFormData] = useState<KnowledgeBaseUpdateData>({
    cleanedData: '',
  });
  const [discardModal, setDiscardModal] = useState(false);

  // Mock data - replace with actual API call
  const { data: knowledgeBaseItem, isLoading } =
    useQuery<KnowledgeBaseDetailData>({
      queryKey: ['knowledge-base', id],
      queryFn: async () => ({
        id: id || '1',
        agency: 'Abc',
        domain: 'Domain 1',
        url: 'https://www.riigiteataja.ee/akt/324092024001',
        cleanedData:
          'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.\n\nDuis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.\n\nSed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto beatae vitae dicta sunt explicabo.',
        lastUpdate: '12.12.2023',
        originallyScraped: '12.12.2023',
      }),
      onSuccess: (data) => {
        setFormData({
          cleanedData: data.cleanedData,
        });
      },
    });

  const updateMutation = useMutation({
    mutationFn: async (data: KnowledgeBaseUpdateData) => {
      await apiDev.put(`knowledge-base/${id}`, data);
    },
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.updateSuccess'),
      });
      navigate('/knowledge-base');
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.updateError'),
      });
    },
  });

  const handleSave = () => {
    updateMutation.mutate(formData);
    onSave();
  };

  const handleCancel = () => {
    navigate('/knowledge-base');
  };

  if (isLoading || !knowledgeBaseItem) {
    return <div>Loading...</div>;
  }
  const otherState1 = editorState === 'raw' ? 'edited' : 'raw';

  const otherState2 =
    editorState === 'edited' || editorState === 'raw' ? 'cleaned' : 'edited';

  return (
    <>
      {discardModal && (
        <Dialog
          title={t('knowledgeBase.discardChanges')}
          onClose={() => setDiscardModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                  fontWeight: 700,
                }}
                onClick={() => setDiscardModal(false)}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="primary"
                onClick={() => {
                  setDiscardModal(false);
                  onCancel();
                }}
              >
                {t('knowledgeBase.discard')}
              </Button>
            </Track>
          }
        >
          {t('knowledgeBase.confirmDiscardChanges')}
        </Dialog>
      )}
      <Card
        header={
          <Track justify="between">
            <div className="knowledge-base-detail__agency-domain">
              {t(`knowledgeBase.${editorState}`)}
            </div>
            <Track gap={10}>
              <Button
                appearance="secondary"
                onClick={() => changeEditorState(otherState1)}
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                  fontWeight: 700,
                }}
              >
                {t('global.view')} {t(`knowledgeBase.${otherState1}`)}
              </Button>

              <Button
                appearance="secondary"
                onClick={() => changeEditorState(otherState2)}
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                  fontWeight: 700,
                }}
              >
                {t('global.view')} {t(`knowledgeBase.${otherState2}`)}
              </Button>
            </Track>
          </Track>
        }
        footer={
          <Track
            gap={16}
            justify="center"
            style={{ justifyContent: 'space-between' }}
          >
            <Button
              appearance="secondary"
              style={{
                color: '#005AA3',
                borderColor: '#005AA3',
                boxShadow: 'inset 0 0 0 2px #005AA3',
              }}
              onClick={() => setDiscardModal(true)}
            >
              {t('global.cancel')}
            </Button>
            <Button
              appearance="primary"
              onClick={handleSave}
              disabled={updateMutation.isLoading}
            >
              {updateMutation.isLoading ? t('global.saving') : t('global.save')}
            </Button>
          </Track>
        }
      >
        <div className="knowledge-base-detail__form-group">
          <div className="knowledge-base-detail__form-label">
            <label className="knowledge-base-detail__label">
              {t('knowledgeBase.url')}
            </label>
          </div>
          <div className="knowledge-base-detail__form-value">
            <span className="knowledge-base-detail__value">
              {knowledgeBaseItem.url}
            </span>
          </div>
        </div>

        <div className="knowledge-base-detail__form-group knowledge-base-detail__form-group--full">
          <div className="knowledge-base-detail__form-label">
            <label className="knowledge-base-detail__label">
              {t('knowledgeBase.cleanedData')}
            </label>
          </div>
          <div className="knowledge-base-detail__form-input">
            <FormTextarea
              label=""
              name="cleanedData"
              hideLabel
              value={formData.cleanedData}
              maxLengthBottom={false}
              minRows={500}
              maxRows={500}
              maxLength={1 / 0}
              disabled={editorState === 'raw'}
              onChange={(e) => {
                if (editorState === 'raw') return;
                setFormData((prev) => ({
                  ...prev,
                  cleanedData: e.target.value,
                }));
              }}
            />
          </div>
        </div>

        <div className="knowledge-base-detail__metadata-row">
          <div className="knowledge-base-detail__form-group knowledge-base-detail__form-group--half">
            <div className="knowledge-base-detail__form-label">
              <label className="knowledge-base-detail__label">
                {t('knowledgeBase.lastUpdate')}
              </label>
            </div>
            <div className="knowledge-base-detail__form-value">
              <span className="knowledge-base-detail__value">
                {knowledgeBaseItem.lastUpdate}
              </span>
            </div>
          </div>

          <div className="knowledge-base-detail__form-group knowledge-base-detail__form-group--half">
            <div className="knowledge-base-detail__form-label">
              <label className="knowledge-base-detail__label">
                {t('knowledgeBase.originallyScraped')}
              </label>
            </div>
            <div className="knowledge-base-detail__form-value">
              <span className="knowledge-base-detail__value">
                {knowledgeBaseItem.originallyScraped}
              </span>
            </div>
          </div>
        </div>
      </Card>
    </>
  );
};

export default KnowledgeBaseDetail;
