import { FC, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  FormInput,
  FormSelect,
  Track,
  Stepper,
  ProgressBar,
} from 'components';
import { useToast } from 'hooks/useToast';
import './FileUpload.scss';

interface FileItem {
  id: string;
  name: string;
  size: number;
  status: 'pending' | 'success' | 'warning' | 'error';
  message?: string;
  url?: string;
}

interface WizardData {
  valdkond: string;
  asutus: string;
  files: FileItem[];
}

const FileUploadWizard: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [currentStep, setCurrentStep] = useState(1);
  const [progress, setProgress] = useState(0);

  const [wizardData, setWizardData] = useState<WizardData>({
    valdkond: '',
    asutus: '',
    files: [],
  });

  const valdkondOptions = [
    { label: '--- Vali valdkond ---', value: '' },
    { label: 'Valdkond 1', value: 'valdkond1' },
    { label: 'Sotsiaalkaitse', value: 'sotsiaalkaitse' },
    { label: 'Tervishoid', value: 'tervishoid' },
  ];

  const asutusOptions = [
    { label: '--- Vali asutus ---', value: '' },
    { label: 'Asutus 1', value: 'asutus1' },
    { label: 'Sotsiaalkindlustusamet', value: 'sotsiaalkindlustusamet' },
    { label: 'Terviseamet', value: 'terviseamet' },
  ];

  const steps = [
    { id: 1, label: t('fileUpload.andmed') || 'Andmed' },
    { id: 2, label: t('fileUpload.salvestamine') || 'Salvestamine' },
  ];

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files) return;

    const newFiles: FileItem[] = Array.from(files).map((file, index) => ({
      id: `file-${Date.now()}-${index}`,
      name: file.name,
      size: file.size,
      status: 'pending' as const,
    }));

    setWizardData((prev) => ({
      ...prev,
      files: [...prev.files, ...newFiles],
    }));
  };

  const handleDeleteFile = (fileId: string) => {
    setWizardData((prev) => ({
      ...prev,
      files: prev.files.filter((file) => file.id !== fileId),
    }));
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const validateFiles = (): FileItem[] => {
    return wizardData.files.map((file) => {
      // Mock validation logic
      if (file.name.toLowerCase().includes('error')) {
        return {
          ...file,
          status: 'error' as const,
          message: 'Faili formaat vale',
        };
      } else if (file.name.toLowerCase().includes('warning')) {
        return {
          ...file,
          status: 'warning' as const,
          message:
            'Sellise nimega fail on juba olemas. Vana sisu kirjutatakse üle.',
          url: 'megadata2.html',
        };
      } else if (file.size > 5000000) {
        // 5MB
        return {
          ...file,
          status: 'error' as const,
          message: 'Fail on liiga suur',
        };
      } else if (file.name.toLowerCase().includes('success')) {
        return {
          ...file,
          status: 'success' as const,
          message: 'Failiga on kõik Norras',
          url: 'megadata1.html',
        };
      } else {
        // Random assignment for demo
        const statuses: ('success' | 'warning' | 'error')[] = [
          'success',
          'warning',
          'error',
        ];
        const randomStatus =
          statuses[Math.floor(Math.random() * statuses.length)];

        let message = '';
        let url = '';

        switch (randomStatus) {
          case 'success':
            message = 'Failiga on kõik Norras';
            url = 'megadata1.html';
            break;
          case 'warning':
            message =
              'Sellise nimega fail on juba olemas, kas kasutan uut faili? Vana sisu kustutakse.';
            url = 'megadata2.html';
            break;
          case 'error':
            message = 'Faili formaat vale';
            break;
        }

        return {
          ...file,
          status: randomStatus,
          message,
          url,
        };
      }
    });
  };

  const handleNext = () => {
    if (currentStep === 1) {
      const validatedFiles = validateFiles();
      setWizardData((prev) => ({ ...prev, files: validatedFiles }));
      setCurrentStep(2);
    }
  };

  const handleBack = () => {
    if (currentStep > 1) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleCancel = () => {
    setCurrentStep(1);
    setWizardData({ valdkond: '', asutus: '', files: [] });
    setProgress(0);
  };

  const handleSave = async () => {
    try {
      setCurrentStep(3); // Go to processing step
      setProgress(0);

      // Simulate processing with incremental progress
      await new Promise((resolve) => setTimeout(resolve, 500));
      setProgress(25);

      await new Promise((resolve) => setTimeout(resolve, 500));
      setProgress(50);

      await new Promise((resolve) => setTimeout(resolve, 500));
      setProgress(75);

      await new Promise((resolve) => setTimeout(resolve, 500));
      setProgress(100);

      await new Promise((resolve) => setTimeout(resolve, 1000));

      toast.open({
        type: 'success',
        title: t('global.notification') || 'Notification',
        message: t('fileUpload.saveSuccess') || 'Files saved successfully',
      });

      // Reset after success
      setCurrentStep(1);
      setWizardData({ valdkond: '', asutus: '', files: [] });
      setProgress(0);
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError') || 'Error',
        message: t('fileUpload.saveError') || 'Error saving files',
      });
      setCurrentStep(2); // Go back to previous step on error
    }
  };

  const renderFileList = () => {
    if (wizardData.files.length === 0) return null;

    return (
      <div className="file-upload__file-list">
        {wizardData.files.map((file) => (
          <div
            key={file.id}
            className={`file-upload__file-item file-upload__file-item--${file.status}`}
          >
            <div className="file-upload__file-info">
              <div className="file-upload__file-name">{file.name}</div>
              <div className="file-upload__file-size">
                {formatFileSize(file.size)}
              </div>
              {file.message && (
                <div className="file-upload__file-message">
                  {file.message}
                  {file.url && (
                    <>
                      <br />
                      <span className="file-upload__file-url">{file.url}</span>
                      {file.status === 'warning' && (
                        <>
                          <br />
                          <button className="file-upload__link-button">
                            {t('fileUpload.ei') || 'Ei'},{' '}
                            {t('fileUpload.kustutan') || 'kustutan'}
                          </button>
                          {' | '}
                          <button className="file-upload__link-button">
                            {t('fileUpload.jah') || 'Jah'},{' '}
                            {t('fileUpload.kasutanUut') || 'kasutan uut'}
                          </button>
                        </>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
            {currentStep === 1 && (
              <button
                className="file-upload__delete-button"
                onClick={() => handleDeleteFile(file.id)}
              >
                {t('global.delete') || 'Delete'}
              </button>
            )}
          </div>
        ))}
      </div>
    );
  };

  const renderStepContent = () => {
    switch (currentStep) {
      case 1:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('fileUpload.andmed') || 'Faili(de) andmed'}
            </h1>
            <div className="file-upload__form">
              <FormSelect
                label={t('fileUpload.valdkond') || 'Valdkond'}
                name="valdkond"
                options={valdkondOptions}
                style={{ display: 'unset' }}
                defaultValue={wizardData.valdkond}
                onSelectionChange={(option) =>
                  setWizardData((prev) => ({
                    ...prev,
                    valdkond: option?.value || '',
                  }))
                }
              />
              <FormSelect
                label={t('fileUpload.asutus') || 'Asutus'}
                name="asutus"
                style={{ display: 'unset' }}
                options={asutusOptions}
                defaultValue={wizardData.asutus}
                onSelectionChange={(option) =>
                  setWizardData((prev) => ({
                    ...prev,
                    asutus: option?.value || '',
                  }))
                }
              />

              <div className="file-upload__upload-section">
                <label className="file-upload__label">
                  {t('fileUpload.valifailSaadmest') || 'Vali fail saadmest'}
                </label>
                <div className="file-upload__upload-area">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    onChange={handleFileChange}
                    style={{ display: 'none' }}
                    accept=".pdf,.doc,.docx,.txt,.html,.htm"
                  />
                  <Button
                    appearance="text"
                    style={{ alignSelf: 'flex-end', width: 'fit-content' }}
                    onClick={handleFileSelect}
                  >
                    + {t('fileUpload.lisaVeelFaile') || 'Lisa veel faile'}
                  </Button>
                </div>
              </div>

              {renderFileList()}
            </div>
          </div>
        );

      case 2:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('fileUpload.kontroll') || 'Kontroll'}
            </h1>
            <div className="file-upload__form">{renderFileList()}</div>
          </div>
        );

      case 3:
        return (
          <ProgressBar
            title={t('fileUpload.processing') || 'Töötlemine käib...'}
            progress={progress}
          />
        );

      default:
        return null;
    }
  };

  const renderButtons = () => {
    switch (currentStep) {
      case 1:
        return (
          <>
            <Button appearance="secondary" onClick={handleCancel}>
              {t('fileUpload.tuhista') || 'Tühista'}
            </Button>
            <Button
              appearance="primary"
              onClick={handleNext}
              disabled={wizardData.files.length === 0}
            >
              {t('fileUpload.jatka') || 'Jätka'}
            </Button>
          </>
        );

      case 2:
        return (
          <>
            <Button appearance="secondary" onClick={handleCancel}>
              {t('fileUpload.tuhista') || 'Tühista'}
            </Button>
            <Button appearance="secondary" onClick={handleBack}>
              {t('fileUpload.tagasi') || 'Tagasi'}
            </Button>
            <Button appearance="primary" onClick={handleSave}>
              {t('fileUpload.salvestan') || 'Salvestan'}
            </Button>
          </>
        );

      case 3:
        return null; // No buttons during processing

      default:
        return null;
    }
  };

  return (
    <div className="file-upload">
      <div className="file-upload__header">
        <h1 className="file-upload__title">
          {t('fileUpload.title') || 'Üles laadimine'}
        </h1>

        <Stepper steps={steps} currentStep={currentStep} />

        <div className="file-upload__header-buttons">{renderButtons()}</div>
      </div>

      <div className="file-upload__content">{renderStepContent()}</div>
    </div>
  );
};

export default FileUploadWizard;
