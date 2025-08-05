import { FC, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Card, FormTextarea, Track, Dialog } from 'components';
import { useToast } from 'hooks/useToast';
import './Editor.scss';
import { EditorState, ScrapedFile } from 'services/files';

interface EditorUpdateData {
  cleanedData: string;
}

interface EditorProps {
  editorState: EditorState;
  changeEditorState: (state: EditorState) => void;
  onCancel: () => void;
  onSave?: (content: string) => void;
  readonly?: boolean;
}

const Editor: FC<EditorProps> = ({
  editorState,
  changeEditorState,
  onCancel,
  onSave,
  readonly = false,
}) => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const [formData, setFormData] = useState<EditorUpdateData>({
    cleanedData: editorState.content,
  });
  const [discardModal, setDiscardModal] = useState(false);

  useEffect(() => {
    setFormData({
      cleanedData: editorState.content,
    });
  }, [editorState.content]);

  // Handle view content switching - delegate to parent
  const handleViewContent = (type: 'raw' | 'cleaned' | 'edited') => {
    changeEditorState({
      ...editorState,
      type,
    });
  };

  const handleSave = () => {
    if (onSave) onSave(formData.cleanedData);
  };

  const handleCancel = () => {
    if (id) {
      navigate('/knowledge-base');
    } else {
      onCancel();
    }
  };

  // Get the other two states that aren't currently active
  const getOtherStates = (
    currentType: 'raw' | 'cleaned' | 'edited'
  ): ['raw' | 'cleaned' | 'edited', 'raw' | 'cleaned' | 'edited'] => {
    const allStates: ('raw' | 'cleaned' | 'edited')[] = [
      'raw',
      'cleaned',
      'edited',
    ];
    const otherStates = allStates.filter((state) => state !== currentType);
    return [otherStates[0], otherStates[1]];
  };

  const [otherState1, otherState2] = getOtherStates(editorState.type);

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
      {editorState.loading ? (
        <div>{t('global.loading')}</div>
      ) : (
        <Card
          header={
            <Track justify="between">
              <div className="knowledge-base-detail__agency-domain">
                {t(`knowledgeBase.${editorState.type}`)}
              </div>
              <Track gap={10}>
                <Button
                  appearance="secondary"
                  onClick={() => handleViewContent(otherState1)}
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
                  onClick={() => handleViewContent(otherState2)}
                  disabled={
                    otherState2 === 'edited'
                      ? !editorState.file?.editedDataUrl
                      : !editorState.file?.cleanedDataUrl
                  }
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
                onClick={() => {
                  if (editorState.content !== formData.cleanedData) {
                    setDiscardModal(true);
                    return;
                  }
                  setDiscardModal(false);
                  onCancel();
                }}
                disabled={editorState.saving}
              >
                {t('global.cancel')}
              </Button>
              {!readonly && onSave && (
                <Button
                  appearance="primary"
                  onClick={handleSave}
                  disabled={editorState.saving}
                >
                  {editorState.saving ? t('global.saving') : t('global.save')}
                </Button>
              )}
            </Track>
          }
        >
          <div className="knowledge-base-detail__form-group">
            <div className="knowledge-base-detail__form-label">
              <label className="knowledge-base-detail__label">
                {editorState.sourceType === 'scraped'
                  ? t('knowledgeBase.url')
                  : t('global.name')}
              </label>
            </div>
            <div className="knowledge-base-detail__form-value">
              <span className="knowledge-base-detail__value">
                {editorState.sourceType === 'scraped'
                  ? editorState.file?.url
                  : editorState.file?.fileName}
              </span>
            </div>
          </div>

          <div className="knowledge-base-detail__form-group knowledge-base-detail__form-group--full">
            <div className="knowledge-base-detail__form-label">
              <label className="knowledge-base-detail__label">
                {editorState.type === 'cleaned'
                  ? t('knowledgeBase.cleanedData')
                  : t('knowledgeBase.editedData')}
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
                disabled={readonly || editorState.saving}
                onChange={(e) => {
                  if (readonly || editorState.saving) return;
                  setFormData((prev) => ({
                    ...prev,
                    cleanedData: e.target.value,
                  }));
                }}
              />
            </div>
          </div>

          {editorState.file && (
            <div className="knowledge-base-detail__metadata-row">
              <div className="knowledge-base-detail__form-group knowledge-base-detail__form-group--half">
                <div className="knowledge-base-detail__form-label">
                  <label className="knowledge-base-detail__label">
                    {t('knowledgeBase.lastUpdate')}
                  </label>
                </div>
                <div className="knowledge-base-detail__form-value">
                  <span className="knowledge-base-detail__value">
                    {new Date(editorState.file.updatedAt).toLocaleDateString(
                      'et-EE',
                      {
                        day: '2-digit',
                        month: '2-digit',
                        year: 'numeric',
                      }
                    )}
                  </span>
                </div>
              </div>

              <div className="knowledge-base-detail__form-group knowledge-base-detail__form-group--half">
                <div className="knowledge-base-detail__form-label">
                  <label className="knowledge-base-detail__label">
                    {editorState.sourceType === 'uploaded'
                      ? t('global.uploaded')
                      : t('knowledgeBase.originallyScraped')}
                  </label>
                </div>
                <div className="knowledge-base-detail__form-value">
                  <span className="knowledge-base-detail__value">
                    {new Date(
                      editorState.sourceType === 'uploaded'
                        ? editorState.file.createdAt
                        : (editorState.file as ScrapedFile).originallyScraped
                    ).toLocaleDateString('et-EE', {
                      day: '2-digit',
                      month: '2-digit',
                      year: 'numeric',
                    })}
                  </span>
                </div>
              </div>
            </div>
          )}
        </Card>
      )}
    </>
  );
};

export default Editor;
