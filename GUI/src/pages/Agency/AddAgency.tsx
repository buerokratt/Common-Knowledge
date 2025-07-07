import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, FormInput, FormSelect, Track } from 'components';
import { useToast } from 'hooks/useToast';
import './AddAgency.scss';
import { Link, useNavigate } from 'react-router-dom';

interface KnowledgeBaseFormData {
  agency: string;
  domain: string;
  content?: string;
  file?: File;
  apiUrl?: string;
  websiteUrl?: string;
}

const agencyOptions = [
  { label: 'Abc', value: 'abc' },
  { label: 'Pvc', value: 'pvc' },
  { label: 'Xyz', value: 'xyz' },
];

const Agency: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();

  const [formData, setFormData] = useState<KnowledgeBaseFormData>({
    agency: '',
    domain: '',
  });

  const handleSaved = () => {
    navigate('/agency/1');
  };
  return (
    <div className="create-agency-container">
      <Track justify="between" align="center">
        <h1 className="h1">{t('global.agency')}</h1>
      </Track>
      <Card
        footer={
          <Track gap={16} justify="between">
            <Link to={'/'}>
              <Button
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3 !important',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                }}
                appearance="secondary"
              >
                {t('global.back')}
              </Button>
            </Link>
            <Button appearance="primary" onClick={handleSaved}>
              {t('global.save')}
            </Button>
          </Track>
        }
      >
        <Track direction="vertical" gap={16} align="right">
          <FormInput
            className="create-agency-container__header-input"
            label={`${t('knowledgeBase.agency')}`}
            name="agency"
            value={formData.agency}
            onChange={(e) =>
              setFormData((prev) => ({ ...prev, agency: e.target.value }))
            }
          />
          <FormInput
            label={`${t('knowledgeBase.sector')}`}
            className="create-agency-container__header-input"
            name="sector"
            value={formData.domain}
            onChange={(e) =>
              setFormData((prev) => ({ ...prev, domain: e.target.value }))
            }
          />
          <Track
            gap={16}
            style={{ width: '100%' }}
            justify="end"
            align="center"
          >
            <label>{t('knowledgeBase.centops')}</label>
            <FormSelect
              label={t('knowledgeBase.agency')}
              name="agency"
              hideLabel
              placeholder={t('global.selectOption')}
              style={{ maxWidth: 808 }}
              options={agencyOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  agency: option?.value ?? '',
                }))
              }
            />
          </Track>
        </Track>
      </Card>
    </div>
  );
};

export default Agency;
