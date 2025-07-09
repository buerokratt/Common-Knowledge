import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { MdOutlineSchedule } from 'react-icons/md';
import {
  Button,
  FormInput,
  FormSelect,
  FormDatepicker,
  SwitchBox,
  Icon,
  Track,
  Card,
} from 'components';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import './Settings.scss';

interface UpdateSettings {
  updateAutomatically: boolean;
  startUpdate: string;
  repeatEvery: number;
  repeatUnit: 'days' | 'weeks' | 'months' | 'years';
  daysOfWeek?: string[];
  dayOfMonth?: number;
  monthOfYear?: string;
  weekPosition?: 'first' | 'second' | 'third' | 'fourth' | 'last';
  dayOfWeek?: string;
  timeOfUpdate: string;
  urls: string[];
}

const KnowledgeBaseSettings: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const { agency, domain } = useParams<{ agency: string; domain: string }>();

  const [settings, setSettings] = useState<UpdateSettings>({
    updateAutomatically: false,
    startUpdate: '',
    repeatEvery: 2,
    repeatUnit: 'days',
    timeOfUpdate: '',
    urls: ['ppa.ee', 'sotsiaalkindlustustusamet.ee'],
  });

  const handleSave = async () => {
    try {
      await apiDev.put(`knowledge-base/${agency}/${domain}/settings`, settings);
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.settingsUpdated'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.settingsUpdateError'),
      });
    }
  };

  const handleCancel = () => {
    // Reset or navigate back
    window.history.back();
  };

  const repeatUnitOptions = [
    { label: t('knowledgeBase.days'), value: 'days' },
    { label: t('knowledgeBase.weeks'), value: 'weeks' },
    { label: t('knowledgeBase.months'), value: 'months' },
    { label: t('knowledgeBase.years'), value: 'years' },
  ];

  const daysOfWeekOptions = [
    { label: t('settings.weekdays.monday'), value: 'Mon' },
    { label: t('settings.weekdays.tuesday'), value: 'Tue' },
    { label: t('settings.weekdays.wednesday'), value: 'Wed' },
    { label: t('settings.weekdays.thursday'), value: 'Thu' },
    { label: t('settings.weekdays.friday'), value: 'Fri' },
    { label: t('settings.weekdays.saturday'), value: 'Sat' },
    { label: t('settings.weekdays.sunday'), value: 'Sun' },
  ];

  const monthOptions = [
    { label: 'January', value: 'January' },
    { label: 'February', value: 'February' },
    { label: 'March', value: 'March' },
    { label: 'April', value: 'April' },
    { label: 'May', value: 'May' },
    { label: 'June', value: 'June' },
    { label: 'July', value: 'July' },
    { label: 'August', value: 'August' },
    { label: 'September', value: 'September' },
    { label: 'October', value: 'October' },
    { label: 'November', value: 'November' },
    { label: 'December', value: 'December' },
  ];

  const weekPositionOptions = [
    { label: t('knowledgeBase.first'), value: 'first' },
    { label: t('knowledgeBase.second'), value: 'second' },
    { label: t('knowledgeBase.third'), value: 'third' },
    { label: t('knowledgeBase.fourth'), value: 'fourth' },
    { label: t('knowledgeBase.last'), value: 'last' },
  ];

  const dayOfWeekOptions = [
    { label: t('settings.weekdays.monday'), value: 'monday' },
    { label: t('settings.weekdays.tuesday'), value: 'tuesday' },
    { label: t('settings.weekdays.wednesday'), value: 'wednesday' },
    { label: t('settings.weekdays.thursday'), value: 'thursday' },
    { label: t('settings.weekdays.friday'), value: 'friday' },
    { label: t('settings.weekdays.saturday'), value: 'saturday' },
    { label: t('settings.weekdays.sunday'), value: 'sunday' },
  ];

  const renderScheduleFields = () => {
    switch (settings.repeatUnit) {
      case 'days':
        return null; // No additional fields for days

      case 'weeks':
        return (
          <div className="knowledge-base-settings__schedule-group">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.dayOfUpdate')}
            </label>
            <div className="knowledge-base-settings__days-grid">
              {daysOfWeekOptions.map((day) => (
                <Button
                  style={{
                    borderRadius: '50%',
                    minHeight: 40,
                    maxHeight: 40,
                    maxWidth: 40,
                    minWidth: 40,
                    padding: 0,
                    fontSize: 14,
                    justifyContent: 'center',
                  }}
                  key={day.value}
                  appearance={
                    settings.daysOfWeek?.includes(day.value)
                      ? 'primary'
                      : 'secondary'
                  }
                  size="s"
                  onClick={() => {
                    const currentDays = settings.daysOfWeek || [];
                    const newDays = currentDays.includes(day.value)
                      ? currentDays.filter((d) => d !== day.value)
                      : [...currentDays, day.value];
                    setSettings((prev) => ({ ...prev, daysOfWeek: newDays }));
                  }}
                >
                  {day.value}
                </Button>
              ))}
            </div>
          </div>
        );

      case 'months':
        return (
          <div
            className="knowledge-base-settings__section"
            style={{
              flexDirection: 'column',
              marginLeft: 318,
              gap: 24,
              alignItems: 'flex-start',
            }}
          >
            <Track gap={8} align="center" style={{ width: 'fit-content' }}>
              <input
                style={{ minWidth: 20 }}
                type="radio"
                id="day-of-month"
                name="month-schedule"
                checked={!settings.weekPosition}
                onChange={() =>
                  setSettings((prev) => ({ ...prev, weekPosition: undefined }))
                }
              />
              <FormInput
                label=""
                name="dayOfMonth"
                type="number"
                min="1"
                max="31"
                value={settings.dayOfMonth?.toString() || '2'}
                hideLabel
                style={{ width: 56 }}
                onChange={(e) =>
                  setSettings((prev) => ({
                    ...prev,
                    dayOfMonth: parseInt(e.target.value),
                  }))
                }
              />
              <span>{t('knowledgeBase.day')}</span>
            </Track>

            <Track gap={8} align="center">
              <input
                type="radio"
                id="week-position"
                name="month-schedule"
                style={{ minWidth: 20 }}
                checked={!!settings.weekPosition}
                onChange={() =>
                  setSettings((prev) => ({ ...prev, weekPosition: 'last' }))
                }
              />
              <FormSelect
                label=""
                name="weekPosition"
                hideLabel
                options={weekPositionOptions}
                style={{ width: 260 }}
                defaultValue={settings.weekPosition || 'last'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    weekPosition: option?.value as
                      | 'first'
                      | 'second'
                      | 'third'
                      | 'fourth'
                      | 'last',
                  }))
                }
              />
              <FormSelect
                label=""
                name="dayOfWeek"
                hideLabel
                style={{ width: 260 }}
                options={dayOfWeekOptions}
                defaultValue={settings.dayOfWeek || 'wednesday'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({ ...prev, dayOfWeek: option?.value }))
                }
              />
            </Track>
          </div>
        );

      case 'years':
        return (
          <div
            className="knowledge-base-settings__section"
            style={{
              flexDirection: 'column',
              marginLeft: 318,
              gap: 24,
              alignItems: 'flex-start',
            }}
          >
            <Track gap={8} align="center">
              <input
                type="radio"
                style={{ minWidth: 20 }}
                id="month-day"
                name="year-schedule"
                checked={!settings.weekPosition}
                onChange={() =>
                  setSettings((prev) => ({ ...prev, weekPosition: undefined }))
                }
              />
              <FormSelect
                label=""
                name="monthOfYear"
                hideLabel
                options={monthOptions}
                style={{ minWidth: 260 }}
                defaultValue={settings.monthOfYear || 'December'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    monthOfYear: option?.value,
                  }))
                }
              />
              <FormInput
                label=""
                name="dayOfMonth"
                type="number"
                min="1"
                max="31"
                value={settings.dayOfMonth?.toString() || '2'}
                hideLabel
                style={{ width: 56 }}
                onChange={(e) =>
                  setSettings((prev) => ({
                    ...prev,
                    dayOfMonth: parseInt(e.target.value),
                  }))
                }
              />
            </Track>

            <Track gap={8} align="center">
              <input
                type="radio"
                id="week-position-year"
                name="year-schedule"
                style={{ minWidth: 20 }}
                checked={!!settings.weekPosition}
                onChange={() =>
                  setSettings((prev) => ({ ...prev, weekPosition: 'last' }))
                }
              />
              <FormSelect
                label=""
                name="weekPosition"
                hideLabel
                options={weekPositionOptions}
                style={{ minWidth: 260 }}
                defaultValue={settings.weekPosition || 'last'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    weekPosition: option?.value as
                      | 'first'
                      | 'second'
                      | 'third'
                      | 'fourth'
                      | 'last',
                  }))
                }
              />
              <FormSelect
                label=""
                name="dayOfWeek"
                hideLabel
                style={{ minWidth: 260 }}
                options={dayOfWeekOptions}
                defaultValue={settings.dayOfWeek || 'wednesday'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({ ...prev, dayOfWeek: option?.value }))
                }
              />
              <FormSelect
                label=""
                name="monthOfYear"
                hideLabel
                options={monthOptions}
                style={{ minWidth: 260 }}
                defaultValue={settings.monthOfYear || 'March'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    monthOfYear: option?.value,
                  }))
                }
              />
            </Track>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="knowledge-base-settings">
      <h1 style={{ marginBottom: 16 }} className="h1">
        {t('knowledgeBase.title')}
      </h1>
      <Card
        header={
          <div>
            <span className="knowledge-base-settings__agency">Abc</span>
            <span className="knowledge-base-settings__separator"> / </span>
            <span className="knowledge-base-settings__domain">Domain</span>
          </div>
        }
        footer={
          <div>
            <Track gap={16} justify="between">
              <Button
                appearance="secondary"
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3 !important',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                }}
                onClick={handleCancel}
              >
                {t('global.cancel')}
              </Button>
              <Button appearance="primary" onClick={handleSave}>
                {t('global.save')}
              </Button>
            </Track>
          </div>
        }
      >
        <div className="knowledge-base-settings__content">
          <div className="knowledge-base-settings__section">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.updateAutomatically')}
            </label>
            <SwitchBox
              label=""
              checked={settings.updateAutomatically}
              onCheckedChange={(checked) =>
                setSettings((prev) => ({
                  ...prev,
                  updateAutomatically: checked,
                }))
              }
            />
          </div>

          {settings.updateAutomatically && (
            <>
              <div className="knowledge-base-settings__section">
                <label className="knowledge-base-settings__label">
                  {t('knowledgeBase.startUpdate')}
                </label>
                <FormInput
                  label={''}
                  name="startUpdate"
                  type="date"
                  style={{ minWidth: 260 }}
                  value={settings.startUpdate}
                  placeholder="pp.kk.aaaa"
                  onChange={(e) =>
                    setSettings((prev) => ({
                      ...prev,
                      startUpdate: e.target.value,
                    }))
                  }
                >
                  {/* <Icon icon={<MdOutlineCalendarMonth />} size="medium" /> */}
                </FormInput>
              </div>

              <div className="knowledge-base-settings__section">
                <div className="knowledge-base-settings__repeat-group">
                  <label className="knowledge-base-settings__label">
                    {t('knowledgeBase.repeatEvery')}
                  </label>
                  <Track gap={16} align="center">
                    <FormInput
                      label=""
                      name="repeatEvery"
                      type="number"
                      min="1"
                      value={settings.repeatEvery.toString()}
                      hideLabel
                      style={{ width: 56 }}
                      onChange={(e) =>
                        setSettings((prev) => ({
                          ...prev,
                          repeatEvery: parseInt(e.target.value),
                        }))
                      }
                    />
                    <FormSelect
                      label=""
                      name="repeatUnit"
                      hideLabel
                      options={repeatUnitOptions}
                      style={{ minWidth: 260 }}
                      defaultValue={settings.repeatUnit}
                      onSelectionChange={(option) =>
                        setSettings((prev) => ({
                          ...prev,
                          repeatUnit: option?.value as
                            | 'days'
                            | 'weeks'
                            | 'months'
                            | 'years',
                        }))
                      }
                    />
                  </Track>
                </div>
              </div>

              {renderScheduleFields()}

              <div className="knowledge-base-settings__section">
                <label className="knowledge-base-settings__label">
                  {t('knowledgeBase.timeOfUpdate')}
                </label>
                <FormInput
                  label={''}
                  name="timeOfUpdate"
                  type="text"
                  value={settings.timeOfUpdate}
                  placeholder="hh:mm"
                  style={{ width: 120 }}
                  onChange={(e) => {
                    let value = e.target.value;

                    // Remove all non-digits
                    let digitsOnly = value.replace(/[^\d]/g, '');

                    // Format based on digits length with native-like validation
                    if (digitsOnly.length === 0) {
                      value = '';
                    } else if (digitsOnly.length === 1) {
                      let firstDigit = parseInt(digitsOnly);
                      // If first digit > 2, treat as 0X (e.g., 3 becomes 03)
                      if (firstDigit > 2) {
                        value = '0' + firstDigit;
                      } else {
                        value = digitsOnly;
                      }
                    } else if (digitsOnly.length === 2) {
                      let hours = parseInt(digitsOnly);
                      // If hours > 23, treat first digit as 0 and second as first digit of new time
                      if (hours > 23) {
                        value =
                          '0' +
                          digitsOnly.charAt(0) +
                          ':' +
                          digitsOnly.charAt(1);
                      } else {
                        value = digitsOnly;
                      }
                    } else if (digitsOnly.length === 3) {
                      let hours = digitsOnly.substring(0, 2);
                      let minute = digitsOnly.charAt(2);

                      // Validate hours
                      if (parseInt(hours) > 23) {
                        hours = '0' + digitsOnly.charAt(0);
                        minute = digitsOnly.charAt(1);
                      }

                      // If first minute digit > 5, treat as 0X
                      if (parseInt(minute) > 5) {
                        minute = '0' + minute;
                      }

                      value = hours + ':' + minute;
                    } else if (digitsOnly.length >= 4) {
                      let hours = digitsOnly.substring(0, 2);
                      let minutes = digitsOnly.substring(2, 4);

                      // Validate hours
                      if (parseInt(hours) > 23) {
                        hours = '0' + digitsOnly.charAt(0);
                        minutes = digitsOnly.charAt(1) + digitsOnly.charAt(2);
                      }

                      // Validate minutes - if > 59, treat first digit as 0X
                      if (parseInt(minutes) > 59) {
                        minutes = '0' + minutes.charAt(0);
                      }

                      value = hours + ':' + minutes;
                    }

                    setSettings((prev) => ({
                      ...prev,
                      timeOfUpdate: value,
                    }));
                  }}
                >
                  <Icon icon={<MdOutlineSchedule />} size="medium" />
                </FormInput>
              </div>
            </>
          )}

          <div className="knowledge-base-settings__section">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.updateManually')}
            </label>
            <Button appearance="primary" onClick={handleSave}>
              {t('knowledgeBase.updateData')}
            </Button>
          </div>

          <div className="knowledge-base-settings__section">
            <div className="knowledge-base-settings__urls">
              <label className="knowledge-base-settings__label">
                {t('knowledgeBase.changeUrlContent')}
              </label>
              <ul className="knowledge-base-settings__url-list">
                {settings.urls.map((url, index) => (
                  <li style={{ padding: 0, margin: 0 }} key={index}>
                    <a
                      href={`https://${url}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {url}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
};

export default KnowledgeBaseSettings;
