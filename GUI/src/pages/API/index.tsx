import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  FormInput,
  FormSelect,
  Track,
  Stepper,
  Icon,
  Tooltip,
} from 'components';
import { useToast } from 'hooks/useToast';
import './API.scss';
import {
  MdCheck,
  MdCheckCircle,
  MdCheckCircleOutline,
  MdDeleteOutline,
  MdRemove,
} from 'react-icons/md';
interface ApiEndpoint {
  id: string;
  name: string;
  serviceType: 'custom' | 'openapi' | '';
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  url: string;
  selectedEndpoint: string;
  params: ApiParam[];
  headers: ApiParam[];
  body: ApiParam[];
  rawBody?: string;
}

interface ApiParam {
  id: string;
  key: string;
  value: string;
  type?: string;
  description?: string;
  required?: boolean;
}

interface WizardData {
  domain: string;
  agency: string;
  endpoints: ApiEndpoint[];
}

const ApiWizard: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  const [currentStep, setCurrentStep] = useState(1);
  const [activeTab, setActiveTab] = useState<'params' | 'headers' | 'body'>(
    'params'
  );
  const [rawDataMode, setRawDataMode] = useState<{ [key: string]: boolean }>(
    {}
  );
  const [testingEndpoint, setTestingEndpoint] = useState<string | null>(null);

  const [wizardData, setWizardData] = useState<WizardData>({
    domain: '',
    agency: '',
    endpoints: [],
  });

  const serviceTypeOptions = [
    { label: t('apiWizard.serviceTypes.custom'), value: 'custom' },
    { label: t('apiWizard.serviceTypes.openapi'), value: 'openapi' },
  ];

  const methodOptions = [
    { label: 'GET', value: 'GET' },
    { label: 'POST', value: 'POST' },
    { label: 'PUT', value: 'PUT' },
    { label: 'DELETE', value: 'DELETE' },
  ];

  const endpointOptions = [
    { label: t('apiWizard.endpointOptions.value1'), value: 'endpoint1' },
    { label: t('apiWizard.endpointOptions.value2'), value: 'endpoint2' },
    { label: t('apiWizard.endpointOptions.value3'), value: 'endpoint3' },
  ];

  const steps = [
    { id: 1, label: t('apiWizard.knowledgebase') },
    { id: 2, label: t('apiWizard.apiConfiguration') },
  ];

  const addEndpoint = () => {
    const newEndpoint: ApiEndpoint = {
      id: `endpoint-${Date.now()}`,
      name: '',
      serviceType: '',
      method: 'GET',
      url: '',
      selectedEndpoint: '',
      params: [],
      headers: [],
      body: [],
      rawBody: '',
    };
    setWizardData((prev) => ({
      ...prev,
      endpoints: [...prev.endpoints, newEndpoint],
    }));
  };

  const updateEndpoint = (
    endpointId: string,
    field: keyof ApiEndpoint,
    value: any
  ) => {
    setWizardData((prev) => ({
      ...prev,
      endpoints: prev.endpoints.map((endpoint) => {
        if (endpoint.id === endpointId) {
          const updated = { ...endpoint, [field]: value };

          // Reset dependent fields when service type changes
          if (field === 'serviceType') {
            updated.name = '';
            updated.url = '';
            updated.selectedEndpoint = '';
            updated.params = [];
            updated.headers = [];
            updated.body = [];
            updated.rawBody = '';
          }

          // Switch to 'params' tab if Body tab becomes unavailable
          if (
            field === 'method' &&
            !['POST', 'PUT', 'PATCH'].includes(value) &&
            activeTab === 'body'
          ) {
            setActiveTab('params');
          }

          return updated;
        }
        return endpoint;
      }),
    }));
  };

  const addParam = (
    endpointId: string,
    type: 'params' | 'headers' | 'body'
  ) => {
    const newParam: ApiParam = {
      id: `param-${Date.now()}`,
      key: '',
      value: '',
    };

    setWizardData((prev) => ({
      ...prev,
      endpoints: prev.endpoints.map((endpoint) =>
        endpoint.id === endpointId
          ? { ...endpoint, [type]: [...endpoint[type], newParam] }
          : endpoint
      ),
    }));
  };

  const updateParam = (
    endpointId: string,
    paramId: string,
    type: 'params' | 'headers' | 'body',
    field: string,
    value: string
  ) => {
    setWizardData((prev) => ({
      ...prev,
      endpoints: prev.endpoints.map((endpoint) =>
        endpoint.id === endpointId
          ? {
              ...endpoint,
              [type]: endpoint[type].map((param) =>
                param.id === paramId ? { ...param, [field]: value } : param
              ),
            }
          : endpoint
      ),
    }));
  };

  const removeParam = (
    endpointId: string,
    paramId: string,
    type: 'params' | 'headers' | 'body'
  ) => {
    setWizardData((prev) => ({
      ...prev,
      endpoints: prev.endpoints.map((endpoint) =>
        endpoint.id === endpointId
          ? {
              ...endpoint,
              [type]: endpoint[type].filter((param) => param.id !== paramId),
            }
          : endpoint
      ),
    }));
  };

  const buildRequestUrl = (endpoint: ApiEndpoint) => {
    let url = endpoint.url;
    const params = endpoint.params.filter((p) => p.key && p.value);

    if (params.length > 0) {
      const queryString = params
        .map(
          (p) => `${encodeURIComponent(p.key)}=${encodeURIComponent(p.value)}`
        )
        .join('&');
      url += (url.includes('?') ? '&' : '?') + queryString;
    }

    return url;
  };

  const buildRequestHeaders = (endpoint: ApiEndpoint) => {
    const headers: Record<string, string> = {};

    endpoint.headers.forEach((h) => {
      if (h.key && h.value) {
        headers[h.key] = h.value;
      }
    });

    // Add content-type for POST/PUT requests with body
    if (
      ['POST', 'PUT'].includes(endpoint.method) &&
      (endpoint.body.length > 0 || endpoint.rawBody)
    ) {
      if (!headers['Content-Type']) {
        headers['Content-Type'] = rawDataMode[endpoint.id]
          ? 'application/json'
          : 'application/x-www-form-urlencoded';
      }
    }

    return headers;
  };

  const buildRequestBody = (endpoint: ApiEndpoint) => {
    if (!['POST', 'PUT'].includes(endpoint.method)) {
      return undefined;
    }

    if (rawDataMode[endpoint.id]) {
      return endpoint.rawBody || '';
    }

    const bodyParams = endpoint.body.filter((p) => p.key && p.value);
    if (bodyParams.length === 0) {
      return undefined;
    }

    const formData = new URLSearchParams();
    bodyParams.forEach((p) => {
      formData.append(p.key, p.value);
    });

    return formData.toString();
  };

  const testEndpoint = async (endpointId: string) => {
    const endpoint = wizardData.endpoints.find((e) => e.id === endpointId);
    if (!endpoint || !endpoint.url) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('apiWizard.validation.enterValidUrl'),
      });
      return;
    }

    setTestingEndpoint(endpointId);

    try {
      const url = buildRequestUrl(endpoint);
      const headers = buildRequestHeaders(endpoint);
      const body = buildRequestBody(endpoint);

      console.log('Testing endpoint:', {
        method: endpoint.method,
        url,
        headers,
        body,
      });

      const response = await fetch(url, {
        method: endpoint.method,
        headers,
        body,
      });

      const responseText = await response.text();
      let responseData;

      try {
        responseData = JSON.parse(responseText);
      } catch {
        responseData = responseText;
      }

      toast.open({
        type: response.ok ? 'success' : 'warning',
        title: t('apiWizard.response.title', { status: response.status }),
        message: t('apiWizard.response.message', {
          method: endpoint.method,
          url: url,
          statusText: response.statusText,
        }),
      });

      console.log('Response:', {
        status: response.status,
        statusText: response.statusText,
        headers: Object.fromEntries(response.headers.entries()),
        data: responseData,
      });
    } catch (error) {
      console.error('Request failed:', error);
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message:
          error instanceof Error
            ? error.message
            : t('apiWizard.errors.requestFailed'),
      });
    } finally {
      setTestingEndpoint(null);
    }
  };

  const fetchEndpoints = (endpointId: string) => {
    toast.open({
      type: 'info',
      title: t('apiWizard.fetching'),
      message: t('apiWizard.fetchingEndpoints'),
    });
  };

  const handleNext = () => {
    if (currentStep === 1) {
      if (!wizardData.domain || !wizardData.agency) {
        toast.open({
          type: 'error',
          title: t('global.notificationError'),
          message: t('apiWizard.validation.fillRequired'),
        });
        return;
      }
      setCurrentStep(2);
      // Add initial endpoint
      if (wizardData.endpoints.length === 0) {
        addEndpoint();
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
    setWizardData({ domain: '', agency: '', endpoints: [] });
    setRawDataMode({});
  };

  const handleSave = () => {
    toast.open({
      type: 'success',
      title: t('global.notification'),
      message: t('apiWizard.success.savedSuccessfully'),
    });
  };

  const toggleRawDataMode = (endpointId: string) => {
    setRawDataMode((prev) => ({
      ...prev,
      [endpointId]: !prev[endpointId],
    }));
  };

  const updateRawBody = (endpointId: string, value: string) => {
    updateEndpoint(endpointId, 'rawBody', value);
  };

  const renderParamSection = (
    endpoint: ApiEndpoint,
    type: 'params' | 'headers' | 'body'
  ) => {
    const params = endpoint[type];
    const isRawMode = type === 'body' && rawDataMode[endpoint.id];

    if (isRawMode) {
      return (
        <div className="api-wizard__raw-data">
          <div className="api-wizard__raw-header">
            <div className="api-wizard__raw-toggle">
              <label>
                <input
                  type="checkbox"
                  checked={rawDataMode[endpoint.id] || false}
                  onChange={() => toggleRawDataMode(endpoint.id)}
                />
                {t('apiWizard.tabs.rawData')}
              </label>
            </div>
          </div>
          <textarea
            placeholder={t('apiWizard.placeholders.rawJson')}
            className="api-wizard__raw-textarea"
            value={endpoint.rawBody || ''}
            onChange={(e) => updateRawBody(endpoint.id, e.target.value)}
          />
        </div>
      );
    }

    return (
      <div className="api-wizard__param-section">
        <div className="api-wizard__param-header">
          <div className="api-wizard__param-columns">
            <span>{t('apiWizard.columns.variable')}</span>
            <span style={{ marginLeft: 255 }}>
              {t('apiWizard.columns.value')}
            </span>
          </div>
          {type === 'body' && (
            <div className="api-wizard__raw-toggle">
              <label>
                <input
                  type="checkbox"
                  checked={rawDataMode[endpoint.id] || false}
                  onChange={() => toggleRawDataMode(endpoint.id)}
                />
                {t('apiWizard.tabs.rawData')}
              </label>
            </div>
          )}
        </div>

        {params.map((param) => (
          <div key={param.id} className="api-wizard__param-row">
            <FormInput
              name={`${param.id}-key`}
              label=""
              hideLabel
              placeholder={t('apiWizard.placeholders.variable')}
              value={param.key}
              onChange={(e) =>
                updateParam(endpoint.id, param.id, type, 'key', e.target.value)
              }
            />

            <FormInput
              name={`${param.id}-value`}
              label=""
              hideLabel
              placeholder={t('apiWizard.placeholders.value')}
              value={param.value}
              onChange={(e) =>
                updateParam(
                  endpoint.id,
                  param.id,
                  type,
                  'value',
                  e.target.value
                )
              }
            />
            <Button
              className="api-wizard__remove-param"
              onClick={() => removeParam(endpoint.id, param.id, type)}
            >
              <Icon icon={<MdDeleteOutline />} size="medium" />
            </Button>
          </div>
        ))}

        <button
          className="api-wizard__add-param"
          onClick={() => addParam(endpoint.id, type)}
        >
          + {t('apiWizard.actions.addParameter')}
        </button>
      </div>
    );
  };

  const renderCustomEndpointFields = (endpoint: ApiEndpoint) => (
    <>
      <FormInput
        name="endpointName"
        className="api-wizard__endpoint-name"
        label={t('apiWizard.endpointName')}
        value={endpoint.name}
        onChange={(e) => updateEndpoint(endpoint.id, 'name', e.target.value)}
      />

      <div className="api-wizard__url-section">
        <label className="api-wizard__url-label">URL</label>
        <div className="api-wizard__url-input">
          <FormSelect
            name="method"
            label=""
            hideLabel
            style={{ minWidth: 108, width: 108, borderTopRightRadius: 0 }}
            options={methodOptions}
            defaultValue={endpoint.method}
            onSelectionChange={(option) =>
              updateEndpoint(endpoint.id, 'method', option?.value || 'GET')
            }
          />
          <FormInput
            name="url"
            label=""
            hideLabel
            placeholder={t('apiWizard.placeholders.enterUrl')}
            value={endpoint.url}
            onChange={(e) => updateEndpoint(endpoint.id, 'url', e.target.value)}
          />
          <Button
            appearance="primary"
            style={{ marginLeft: 8 }}
            onClick={() => testEndpoint(endpoint.id)}
            disabled={testingEndpoint === endpoint.id}
          >
            {testingEndpoint === endpoint.id
              ? t('apiWizard.testing')
              : t('apiWizard.testUrl')}
          </Button>
        </div>
      </div>

      {/* Tabs and Parameters */}
      <div className="api-wizard__endpoint-body">
        <div className="api-wizard__tabs">
          <button
            className={`api-wizard__tab ${
              activeTab === 'params' ? 'api-wizard__tab--active' : ''
            }`}
            onClick={() => setActiveTab('params')}
          >
            {t('apiWizard.tabs.params')}
          </button>
          <button
            className={`api-wizard__tab ${
              activeTab === 'headers' ? 'api-wizard__tab--active' : ''
            }`}
            onClick={() => setActiveTab('headers')}
          >
            {t('apiWizard.tabs.headers')}
          </button>
          {/* Only show Body tab for methods that support request bodies */}
          {['POST', 'PUT', 'PATCH'].includes(endpoint.method) && (
            <button
              className={`api-wizard__tab ${
                activeTab === 'body' ? 'api-wizard__tab--active' : ''
              }`}
              onClick={() => setActiveTab('body')}
            >
              {t('apiWizard.tabs.body')}
            </button>
          )}
        </div>

        <div className="api-wizard__tab-content">
          {renderParamSection(endpoint, activeTab)}
        </div>
      </div>
    </>
  );

  const renderOpenApiFields = (endpoint: ApiEndpoint) => (
    <>
      <FormInput
        name="endpointName"
        className="api-wizard__endpoint-name"
        label={t('apiWizard.endpointName')}
        value={endpoint.name}
        onChange={(e) => updateEndpoint(endpoint.id, 'name', e.target.value)}
      />

      <div className="api-wizard__url-section">
        <label className="api-wizard__url-label">URL</label>
        <div className="api-wizard__url-input-simple">
          <FormInput
            name="url"
            label=""
            hideLabel
            placeholder={t('apiWizard.placeholders.enterApiEndpoint')}
            value={endpoint.url}
            onChange={(e) => updateEndpoint(endpoint.id, 'url', e.target.value)}
          />
          <Button
            appearance="primary"
            onClick={() => fetchEndpoints(endpoint.id)}
          >
            {t('apiWizard.fetchEndpoints')}
          </Button>
        </div>
      </div>

      <div className="api-wizard__endpoint-select">
        <FormSelect
          name="endpoint"
          label={t('apiWizard.endpoint')}
          style={{ display: 'unset' }}
          placeholder={t('global.choose')}
          options={endpointOptions}
          defaultValue={endpoint.selectedEndpoint}
          onSelectionChange={(option) =>
            updateEndpoint(endpoint.id, 'selectedEndpoint', option?.value || '')
          }
        />
      </div>

      {/* Show response mapping if endpoint is selected */}
      {endpoint.selectedEndpoint && (
        <div className="api-wizard__response-data">
          <div className="api-wizard__response-header">
            <div className="api-wizard__param-columns">
              <span>{t('apiWizard.columns.variable')}</span>
              <span>{t('apiWizard.columns.value')}</span>
            </div>
          </div>

          <div className="api-wizard__response-row">
            <span className="api-wizard__response-key">
              idCode, (string), (description)
            </span>
            <FormSelect
              name="response1"
              label=""
              hideLabel
              placeholder={t('apiWizard.endpointOptions.value1')}
              options={endpointOptions}
              defaultValue="endpoint1"
            />
            <Button disabled className="api-wizard__response-info">
              <Icon
                icon={<MdCheckCircleOutline fontSize={20} />}
                size="medium"
              />
            </Button>
          </div>

          <div className="api-wizard__response-row">
            <span className="api-wizard__response-key">
              PetID, name, status
            </span>
            <FormSelect
              name="response2"
              label=""
              hideLabel
              placeholder={t('apiWizard.endpointOptions.value2')}
              options={endpointOptions}
              defaultValue="endpoint2"
            />
            <Button className="api-wizard__remove-param">
              <Icon icon={<MdDeleteOutline />} size="medium" />
            </Button>
          </div>

          <div className="api-wizard__response-row">
            <span className="api-wizard__response-key">
              idCode, (string), (description)
            </span>
            <FormSelect
              name="response3"
              label=""
              hideLabel
              placeholder={t('apiWizard.endpointOptions.value3')}
              options={endpointOptions}
              defaultValue="endpoint3"
            />
            <Button className="api-wizard__remove-param">
              <Icon icon={<MdDeleteOutline />} size="medium" />
            </Button>
          </div>
        </div>
      )}
    </>
  );
  const removeEndpoint = (endpointId: string) => {
    // Prevent removal if it's the last endpoint
    if (wizardData.endpoints.length === 1) {
      toast.open({
        type: 'warning',
        title: t('global.warning'),
        message: t('apiWizard.validation.atLeastOneEndpoint'), // You'll need to add this translation
      });
      return;
    }

    setWizardData((prev) => ({
      ...prev,
      endpoints: prev.endpoints.filter(
        (endpoint) => endpoint.id !== endpointId
      ),
    }));

    // If we were testing this endpoint, clear the testing state
    if (testingEndpoint === endpointId) {
      setTestingEndpoint(null);
    }

    // Clear raw data mode for this endpoint
    setRawDataMode((prev) => {
      const newMode = { ...prev };
      delete newMode[endpointId];
      return newMode;
    });
  };

  const renderEndpointConfiguration = (endpoint: ApiEndpoint) => (
    <div key={endpoint.id} className="api-wizard__endpoint">
      <div className="api-wizard__endpoint-remove">
        <Tooltip
          content={
            wizardData.endpoints.length === 1
              ? t('apiWizard.validation.atLeastOneEndpoint')
              : t('apiWizard.removeEndpoint')
          }
        >
          <div style={{ position: 'absolute' }}>
            <Button
              appearance="text"
              className="api-wizard__remove-endpoint"
              onClick={() => removeEndpoint(endpoint.id)}
              disabled={wizardData.endpoints.length === 1}
            >
              <Icon icon={<MdRemove fontSize={20} />} size="medium" />
            </Button>
          </div>
        </Tooltip>
      </div>
      <FormSelect
        name="serviceType"
        label={t('apiWizard.serviceType')}
        options={serviceTypeOptions}
        placeholder={t('global.choose')}
        style={{ display: 'unset' }}
        defaultValue={endpoint.serviceType}
        onSelectionChange={(option) =>
          updateEndpoint(endpoint.id, 'serviceType', option?.value || '')
        }
      />

      {/* Conditionally render fields based on service type */}
      {endpoint.serviceType === 'custom' &&
        renderCustomEndpointFields(endpoint)}
      {endpoint.serviceType === 'openapi' && renderOpenApiFields(endpoint)}
    </div>
  );

  const renderStepContent = () => {
    switch (currentStep) {
      case 1:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('apiWizard.knowledgebase')}
            </h1>
            <div className="api-wizard__form">
              <FormInput
                label={t('apiWizard.domain')}
                name="domain"
                value={wizardData.domain}
                onChange={(e) =>
                  setWizardData((prev) => ({ ...prev, domain: e.target.value }))
                }
              />
              <FormInput
                label={t('apiWizard.agency')}
                name="agency"
                value={wizardData.agency}
                onChange={(e) =>
                  setWizardData((prev) => ({ ...prev, agency: e.target.value }))
                }
              />
            </div>
          </div>
        );

      case 2:
        return (
          <div>
            <h1 style={{ marginBottom: 16 }} className="h1">
              {t('apiWizard.apiConfiguration')}
            </h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <span className="api-wizard__live-label">
                {t('apiWizard.liveEndpoints')}
              </span>
              <div className="api-wizard__live-endpoints">
                <Button
                  appearance="text"
                  onClick={addEndpoint}
                  className="api-wizard__add-test"
                >
                  + {t('apiWizard.addTest')}
                </Button>
              </div>
            </div>
            <div className="api-wizard__form">
              {wizardData.endpoints.map((endpoint) => (
                <div key={endpoint.id} className="api-wizard__endpoint-card">
                  {renderEndpointConfiguration(endpoint)}
                </div>
              ))}
            </div>
            <Button
              appearance="text"
              onClick={addEndpoint}
              className="api-wizard__add-endpoint"
            >
              + {t('apiWizard.addEndpoint')}
            </Button>
          </div>
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
              {t('global.cancel')}
            </Button>
            <Button appearance="primary" onClick={handleNext}>
              {t('apiWizard.continue')}
            </Button>
          </>
        );

      case 2:
        return (
          <>
            <Button appearance="secondary" onClick={handleCancel}>
              {t('global.cancel')}
            </Button>
            <Button appearance="secondary" onClick={handleBack}>
              {t('global.back')}
            </Button>
            <Button appearance="primary" onClick={handleSave}>
              {t('apiWizard.continue')}
            </Button>
          </>
        );

      default:
        return null;
    }
  };

  return (
    <div className="api-wizard">
      <div className="api-wizard__header">
        <h1 className="api-wizard__title">{t('apiWizard.title')}</h1>

        <Stepper steps={steps} currentStep={currentStep} />

        <div className="api-wizard__header-buttons">{renderButtons()}</div>
      </div>

      <div className="api-wizard__content">{renderStepContent()}</div>
    </div>
  );
};

export default ApiWizard;
