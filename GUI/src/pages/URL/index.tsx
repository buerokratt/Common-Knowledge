import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  FormInput,
  FormSelect,
  DataTable,
  Track,
  Stepper,
  ProgressBar,
} from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import './URL.scss';

interface UrlItem {
  id: string;
  url: string;
  selected?: boolean;
  inSystem?: boolean;
}

interface WizardData {
  valdkond: string;
  asutus: string;
  url: string;
  selectedUrls: string[];
}

const UrlWizard: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  const [currentStep, setCurrentStep] = useState(1);
  const [progress, setProgress] = useState(0);
  const [selectedRows, setSelectedRows] = useState<string[]>([]);
  const [allSelected, setAllSelected] = useState(false);

  const [wizardData, setWizardData] = useState<WizardData>({
    valdkond: '',
    asutus: '',
    url: '',
    selectedUrls: [],
  });

  // Mock data with system status
  const urlData: UrlItem[] = [
    { id: '1', url: 'https://sotsiaalkindlustustusamet.ee/', inSystem: true },
    {
      id: '2',
      url: 'https://sotsiaalkindlustustusamet.ee/123',
      inSystem: true,
    },
    {
      id: '3',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '4',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '5',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '6',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '7',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '8',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '9',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '10',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
    {
      id: '11',
      url: 'https://sotsiaalkindlustustusamet.ee/456',
      inSystem: false,
    },
  ];

  const valdkondOptions = [
    { label: '--- Vali valdkond ---', value: '' },
    { label: 'Sotsiaalkaitse', value: 'sotsiaalkaitse' },
    { label: 'Tervishoid', value: 'tervishoid' },
  ];

  const asutusOptions = [
    { label: '--- Vali asutus ---', value: '' },
    { label: 'Sotsiaalkindlustusamet', value: 'sotsiaalkindlustusamet' },
    { label: 'Terviseamet', value: 'terviseamet' },
  ];

  const steps = [
    { id: 1, label: t('urlWizard.andmed') },
    { id: 2, label: t('urlWizard.ridadeValimine') },
    { id: 3, label: t('urlWizard.salvestamine') },
  ];

  const handleNext = () => {
    if (currentStep < 3) {
      setCurrentStep(currentStep + 1);
      if (currentStep === 2) {
        setWizardData((prev) => ({ ...prev, selectedUrls: selectedRows }));
      }
    }
  };

  const handleBack = () => {
    if (currentStep > 1) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleCancel = () => {
    setCurrentStep(1);
    setWizardData({ valdkond: '', asutus: '', url: '', selectedUrls: [] });
    setSelectedRows([]);
    setAllSelected(false);
  };

  const handleSave = async () => {
    try {
      setProgress(50);
      await new Promise((resolve) => setTimeout(resolve, 2000)); // Simulate saving

      setCurrentStep(4); // Go to scraping step
      setProgress(50);
      await new Promise((resolve) => setTimeout(resolve, 2000)); // Simulate scraping

      setProgress(100);
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('urlWizard.saveSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('urlWizard.saveError'),
      });
    }
  };

  const handleSelectAll = () => {
    if (allSelected) {
      setSelectedRows([]);
      setAllSelected(false);
    } else {
      setSelectedRows(urlData.map((item) => item.id));
      setAllSelected(true);
    }
  };

  const handleRowSelect = (id: string) => {
    setSelectedRows((prev) => {
      const newSelection = prev.includes(id)
        ? prev.filter((rowId) => rowId !== id)
        : [...prev, id];
      setAllSelected(newSelection.length === urlData.length);
      return newSelection;
    });
  };

  // Columns for step 2
  const step2Columns: ColumnDef<UrlItem>[] = [
    {
      id: 'select',
      header: () => (
        <input
          type="checkbox"
          checked={allSelected}
          onChange={handleSelectAll}
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedRows.includes(row.original.id)}
          onChange={() => handleRowSelect(row.original.id)}
        />
      ),
    },
    {
      accessorKey: 'url',
      header: 'URL',
      cell: ({ row }) => <span>{row.original.url}</span>,
    },
  ];

  // Columns for step 3 (saving preview)
  const step3Columns: ColumnDef<UrlItem>[] = [
    {
      id: 'select',
      header: () => (
        <input
          type="checkbox"
          checked={allSelected}
          onChange={handleSelectAll}
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedRows.includes(row.original.id)}
          onChange={() => handleRowSelect(row.original.id)}
        />
      ),
    },
    {
      accessorKey: 'url',
      header: 'URL',
      cell: ({ row }) => <span>{row.original.url}</span>,
    },
    {
      id: 'system',
      accessorKey: 'inSystem',
      header: 'Süsteemis',
      enableColumnFilter: false,
      cell: ({ row }) => {
        const dotColor = row.original.inSystem ? '#308653' : '#B3B3B3';

        return (
          <div
            style={{
              width: 16,
              height: 16,
              borderRadius: '50%',
              backgroundColor: dotColor,
            }}
          />
        );
      },
    },
  ];

  const renderStepContent = () => {
    switch (currentStep) {
      case 1:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('urlWizard.andmed')}
            </h1>
            <div className="url-wizard__form">
              <FormSelect
                label={t('urlWizard.valdkond')}
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
                label={t('urlWizard.asutus')}
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
              <FormInput
                label="URL"
                name="url"
                className="url-input"
                type="url"
                value={wizardData.url}
                onChange={(e) =>
                  setWizardData((prev) => ({ ...prev, url: e.target.value }))
                }
              />
            </div>
          </div>
        );

      case 2:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('urlWizard.ridadeValimine')}
            </h1>
            <div className="url-wizard__form">
              {selectedRows.length > 0 && (
                <div className="url-wizard__selection-info">
                  <span>{selectedRows.length} rows selected</span>
                  <Button
                    appearance="primary"
                    size="s"
                    onClick={handleSelectAll}
                  >
                    {t('urlWizard.selectAllRows', { count: 170 })}
                  </Button>
                </div>
              )}
              <DataTable
                data={urlData}
                columns={step2Columns}
                pagination={{
                  pageIndex: 0,
                  pageSize: 10,
                }}
                filterable
                sortable
                pagesCount={Math.ceil(170 / 10)}
              />
              <div className="url-wizard__results">
                170 {t('knowledgeBase.results')}
              </div>
            </div>
          </div>
        );

      case 3:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('urlWizard.salvestamine')}
            </h1>
            <div className="url-wizard__form">
              <div className="url-wizard__selection-info">
                <span>
                  Selected {wizardData.selectedUrls.length} rows. Existing rows
                  are not selected.
                </span>
              </div>

              <DataTable
                data={urlData.filter((item) =>
                  wizardData.selectedUrls.includes(item.id)
                )}
                columns={step3Columns}
                pagination={{
                  pageIndex: 0,
                  pageSize: 10,
                }}
                filterable
                sortable
                pagesCount={Math.ceil(wizardData.selectedUrls.length / 10)}
              />
              <div className="url-wizard__results">
                {wizardData.selectedUrls.length} {t('knowledgeBase.results')}
              </div>
            </div>
          </div>
        );

      case 4:
        return (
          <ProgressBar
            title={t('urlWizard.scraping') || 'Töötlemine käib...'}
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
              {t('urlWizard.tuhista')}
            </Button>
            <Button appearance="primary" onClick={handleNext}>
              {t('urlWizard.jatka')}
            </Button>
          </>
        );

      case 2:
        return (
          <>
            <Button appearance="secondary" onClick={handleCancel}>
              {t('urlWizard.tuhista')}
            </Button>
            <Button appearance="secondary" onClick={handleBack}>
              {t('urlWizard.tagasi')}
            </Button>
            <Button appearance="primary" onClick={handleNext}>
              {t('urlWizard.jatka')}
            </Button>
          </>
        );

      case 3:
        return (
          <>
            <Button appearance="secondary" onClick={handleCancel}>
              {t('urlWizard.tuhista')}
            </Button>
            <Button appearance="secondary" onClick={handleBack}>
              {t('urlWizard.tagasi')}
            </Button>
            <Button appearance="primary" onClick={handleSave}>
              {t('urlWizard.salvestan')}
            </Button>
          </>
        );

      case 4:
        return null; // No buttons during processing

      default:
        return null;
    }
  };

  return (
    <div className="url-wizard">
      <div className="url-wizard__header">
        <h1 className="url-wizard__title">{t('urlWizard.title')}</h1>

        <Stepper steps={steps} currentStep={currentStep} />

        <div className="url-wizard__header-buttons">{renderButtons()}</div>
      </div>

      <div className="url-wizard__content">{renderStepContent()}</div>
    </div>
  );
};

export default UrlWizard;
