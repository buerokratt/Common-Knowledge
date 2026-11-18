import { FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, FormTextarea, Track } from 'components';
import './Editor.scss';
import { EditorState, ScrapedFile } from 'services/files';

interface EditorProps {
  editorState: EditorState;
  changeEditorState: (state: EditorState) => void;
  onCancel: () => void;
}

// View-only: shows the cleaned content of a file. Manual editing was removed
// (#472), so the knowledge base keeps only raw and cleaned versions.
const Editor: FC<EditorProps> = ({
  editorState,
  changeEditorState,
  onCancel,
}) => {
  const { t } = useTranslation();

  // Handle view content switching - delegate to parent
  const handleViewContent = (type: 'raw' | 'cleaned') => {
    changeEditorState({
      ...editorState,
      type,
    });
  };

  const otherState: 'raw' | 'cleaned' =
    editorState.type === 'cleaned' ? 'raw' : 'cleaned';

  return (
    <>
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
                  onClick={() => handleViewContent(otherState)}
                  disabled={
                    otherState === 'cleaned' && !editorState.file?.cleanedDataUrl
                  }
                  style={{
                    color: '#005AA3',
                    borderColor: '#005AA3',
                    boxShadow: 'inset 0 0 0 2px #005AA3',
                    fontWeight: 700,
                  }}
                >
                  {t('global.view')} {t(`knowledgeBase.${otherState}`)}
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
                onClick={onCancel}
              >
                {t('global.cancel')}
              </Button>
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
                {t('knowledgeBase.cleanedData')}
              </label>
            </div>
            <div className="knowledge-base-detail__form-input">
              <FormTextarea
                label=""
                name="cleanedData"
                hideLabel
                value={editorState.content}
                maxLengthBottom={false}
                minRows={500}
                maxRows={500}
                maxLength={1 / 0}
                disabled
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
