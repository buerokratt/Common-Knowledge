import { FC, useState, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, FormInput, FormSelect, Track } from 'components';
import { useToast } from 'hooks/useToast';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  createAgency,
  updateAgency,
  getAgency,
  CreateAgencyRequest,
  UpdateAgencyRequest,
  Agency,
} from 'services/agencies';
import './SaveAgency.scss';

interface AgencyFormData {
  name: string;
  sector: string;
}

const SaveAgency: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams<{ id: string }>();

  // Determine if we're in edit mode
  const isEditMode = Boolean(id);

  const [formData, setFormData] = useState<AgencyFormData>({
    name: '',
    sector: '',
  });

  const [formErrors, setFormErrors] = useState<Partial<AgencyFormData>>({});

  // Fetch existing agency data if in edit mode
  const {
    data: existingAgency,
    isLoading: isLoadingAgency,
    error: agencyError,
  } = useQuery({
    queryKey: ['agency', id],
    queryFn: () => getAgency(id!),
    enabled: isEditMode,
    onSuccess: (agency: Agency) => {
      // Populate form with existing data
      setFormData({
        name: agency.name,
        sector: agency.sector,
      });
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.agencyLoadError'),
      });
    },
  });

  // Create agency mutation
  const createAgencyMutation = useMutation({
    mutationFn: createAgency,
    onSuccess: (data) => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.agencyCreated'),
      });

      // Invalidate and refetch agencies list
      queryClient.invalidateQueries(['agencies']);
      // Navigate to the created agency detail page
      navigate(`/agency/${data.baseId}`);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.response?.status === 409
          ? t('knowledgeBase.agencyAlreadyExists')
          : error.message || t('knowledgeBase.agencyCreateError'),
      });
    },
  });

  // Update agency mutation
  const updateAgencyMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateAgencyRequest }) =>
      updateAgency(id, data),
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.agencyUpdated'),
      });

      // Invalidate and refetch agencies list
      queryClient.invalidateQueries(['agencies']);
      queryClient.invalidateQueries(['agency', id]);
      // Navigate back to agency detail or list
      navigate(`/agency/${id}`);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.agencyUpdateError'),
      });
    },
  });

  // Form validation
  const validateForm = (): boolean => {
    const errors: Partial<AgencyFormData> = {};

    if (!formData.name.trim()) {
      errors.name = t('validation.required');
    }

    if (!formData.sector.trim()) {
      errors.sector = t('validation.required');
    }

    // externalId is optional - no validation required

    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSave = () => {
    if (!validateForm()) {
      return;
    }

    const requestData = {
      name: formData.name.trim(),
      sector: formData.sector.trim(),
    };

    if (isEditMode) {
      updateAgencyMutation.mutate({
        id: id!,
        data: requestData as UpdateAgencyRequest,
      });
    } else {
      createAgencyMutation.mutate(requestData as CreateAgencyRequest);
    }
  };

  const handleInputChange = (field: keyof AgencyFormData, value: string) => {
    setFormData((prev) => ({ ...prev, [field]: value }));

    // Clear error when user starts typing
    if (formErrors[field]) {
      setFormErrors((prev) => ({ ...prev, [field]: undefined }));
    }
  };

  // Check if form is complete
  const isFormComplete = useMemo(() => {
    return formData.name.trim() !== '' && formData.sector.trim() !== '';
  }, [formData.name, formData.sector]);

  // Check if form has changes (for edit mode)
  const hasChanges = useMemo(() => {
    if (!isEditMode || !existingAgency) return true;

    return (
      formData.name !== existingAgency.name ||
      formData.sector !== existingAgency.sector
    );
  }, [formData, existingAgency, isEditMode]);

  const isLoading =
    createAgencyMutation.isLoading || updateAgencyMutation.isLoading;
  const isSaveDisabled =
    isLoading || !isFormComplete || (isEditMode && !hasChanges);

  // Show loading state while fetching agency data in edit mode
  if (isEditMode && isLoadingAgency) {
    return (
      <div className="create-agency-container">
        <div>{t('global.loading')}</div>
      </div>
    );
  }

  // Show error state if agency fetch failed
  if (isEditMode && agencyError) {
    return (
      <div className="create-agency-container">
        <Link to="/agency">
          <Button appearance="secondary">{t('global.back')}</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="create-agency-container">
      <Track justify="between" align="center">
        <h1 className="h1">
          {isEditMode ? t('global.agency') : t('knowledgeBase.addAgency')}
        </h1>
      </Track>

      <Card
        footer={
          <Track gap={16} justify="between">
            <Link to={'/agency'}>
              <Button
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3 !important',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                }}
                appearance="secondary"
                disabled={isLoading}
              >
                {t('global.back')}
              </Button>
            </Link>
            <Button
              appearance="primary"
              onClick={handleSave}
              disabled={isSaveDisabled}
            >
              {isLoading ? t('global.saving') : t('global.save')}
            </Button>
          </Track>
        }
      >
        <Track direction="vertical" gap={16} align="right">
          <FormInput
            className="create-agency-container__header-input"
            label={`${t('knowledgeBase.agency')}`}
            name="name"
            value={formData.name}
            onChange={(e) => handleInputChange('name', e.target.value)}
            error={formErrors.name}
            required
          />

          <FormInput
            label={`${t('knowledgeBase.sector')}`}
            className="create-agency-container__header-input"
            name="sector"
            value={formData.sector}
            onChange={(e) => handleInputChange('sector', e.target.value)}
            error={formErrors.sector}
            required
          />

        </Track>
      </Card>
    </div>
  );
};

export default SaveAgency;
