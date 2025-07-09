import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { MdOutlineDeleteOutline } from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  FormSelect,
  FormTextarea,
  Icon,
  Track,
} from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import './AgencyList.scss';
import { Link } from 'react-router-dom';

interface KnowledgeBaseItem {
  id: string;
  agency: string;
  domain: string;
  lastUpdate: string;
}

interface KnowledgeBaseFormData {
  agency: string;
  domain: string;
  content?: string;
  file?: File;
  apiUrl?: string;
  websiteUrl?: string;
}

const Agency: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  const [uploadModal, setUploadModal] = useState(false);
  const [addApiModal, setAddApiModal] = useState(false);
  const [addUrlModal, setAddUrlModal] = useState(false);
  const [editModal, setEditModal] = useState<KnowledgeBaseItem | null>(null);
  const [deleteModal, setDeleteModal] = useState<KnowledgeBaseItem | null>(
    null
  );

  const [formData, setFormData] = useState<KnowledgeBaseFormData>({
    agency: '',
    domain: '',
  });

  // Mock data - replace with actual API call
  const { data: knowledgeBaseData, refetch } = useQuery<{
    data: KnowledgeBaseItem[];
    total: number;
  }>({
    queryKey: ['knowledge-base'],
    queryFn: async () => ({
      data: [
        {
          id: '1',
          agency: 'Abc',
          domain: 'Domain 1',
          lastUpdate: '31.04.2025',
        },
        {
          id: '2',
          agency: 'Pvc',
          domain: 'Domain 2',
          lastUpdate: '31.04.2025',
        },
        {
          id: '3',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '4',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '5',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '6',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '7',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '8',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '9',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '10',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
        {
          id: '11',
          agency: 'Xyz',
          domain: 'Domain 3',
          lastUpdate: '31.04.2025',
        },
      ],
      total: 170,
    }),
  });

  const handleUpload = async () => {
    try {
      // Implement file upload logic
      await apiDev.post('knowledge-base/upload', formData);
      setUploadModal(false);
      setFormData({ agency: '', domain: '' });
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.uploadSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.uploadError'),
      });
    }
  };

  const handleAddApi = async () => {
    try {
      // Implement API integration logic
      await apiDev.post('knowledge-base/api', formData);
      setAddApiModal(false);
      setFormData({ agency: '', domain: '' });
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.apiSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.apiError'),
      });
    }
  };

  const handleAddUrl = async () => {
    try {
      // Implement URL integration logic
      await apiDev.post('knowledge-base/url', formData);
      setAddUrlModal(false);
      setFormData({ agency: '', domain: '' });
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.urlError'),
      });
    }
  };

  const handleEdit = (item: KnowledgeBaseItem) => {
    setEditModal(item);
    setFormData({
      agency: item.agency,
      domain: item.domain,
    });
  };

  const handleUpdateItem = async () => {
    if (!editModal) return;

    try {
      await apiDev.put(`knowledge-base/${editModal.id}`, formData);
      setEditModal(null);
      setFormData({ agency: '', domain: '' });
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.updateSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.updateError'),
      });
    }
  };

  const handleDelete = async () => {
    if (!deleteModal) return;

    try {
      await apiDev.delete(`knowledge-base/${deleteModal.id}`);
      setDeleteModal(null);
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.deleteSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.deleteError'),
      });
    }
  };

  const columns: ColumnDef<KnowledgeBaseItem>[] = [
    {
      accessorKey: 'agency',
      header: t('knowledgeBase.agency'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Link
          to={`/agency/${row.original.id}`}
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <div className="agencies__agency-cell">{row.original.agency}</div>
        </Link>
      ),
    },
    {
      accessorKey: 'domain',
      header: t('knowledgeBase.sector'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'lastUpdate',
      header: t('knowledgeBase.lastUpdate'),
      enableColumnFilter: false,
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={32} justify="end">
          <Button
            appearance="text"
            onClick={() => setDeleteModal(row.original)}
            className="agencies__action-btn"
          >
            <Icon
              icon={<MdOutlineDeleteOutline fontSize={20} />}
              size="medium"
            />
            {t('global.delete')}
          </Button>
        </Track>
      ),
    },
  ];

  const agencyOptions = [
    { label: 'Abc', value: 'abc' },
    { label: 'Pvc', value: 'pvc' },
    { label: 'Xyz', value: 'xyz' },
  ];

  const domainOptions = [
    { label: 'Domain 1', value: 'domain1' },
    { label: 'Domain 2', value: 'domain2' },
    { label: 'Domain 3', value: 'domain3' },
  ];

  return (
    <div className="agencies">
      <Track
        style={{ marginBottom: 16, minWidth: 800 }}
        justify="between"
        align="center"
      >
        <h1 className="h1">{t('knowledgeBase.agencies')}</h1>
        <Track gap={12}>
          <Link to="/agency/add">
            <Button appearance="primary">{t('knowledgeBase.addAgency')}</Button>
          </Link>
        </Track>
      </Track>
      <Card>
        <DataTable
          data={knowledgeBaseData?.data ?? []}
          columns={columns}
          pagination={{
            pageIndex: 0,
            pageSize: 10,
          }}
          sortable
          filterable
          pagesCount={Math.ceil((knowledgeBaseData?.total ?? 0) / 10)}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {knowledgeBaseData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>

      {/* Upload Modal */}
      {uploadModal && (
        <Dialog
          title={t('knowledgeBase.uploadTitle')}
          onClose={() => setUploadModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setUploadModal(false)}
              >
                {t('global.cancel')}
              </Button>
              <Button appearance="primary" onClick={handleUpload}>
                {t('knowledgeBase.upload')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormSelect
              label={t('knowledgeBase.agency')}
              name="agency"
              options={agencyOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  agency: option?.value ?? '',
                }))
              }
            />
            <FormSelect
              label={t('knowledgeBase.domain')}
              name="domain"
              options={domainOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  domain: option?.value ?? '',
                }))
              }
            />
            <FormInput
              label={t('knowledgeBase.file')}
              name="file"
              type="file"
              onChange={(e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                setFormData((prev) => ({ ...prev, file }));
              }}
            />
          </Track>
        </Dialog>
      )}

      {/* Add API Modal */}
      {addApiModal && (
        <Dialog
          title={t('knowledgeBase.addApiTitle')}
          onClose={() => setAddApiModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setAddApiModal(false)}
              >
                {t('global.cancel')}
              </Button>
              <Button appearance="primary" onClick={handleAddApi}>
                {t('knowledgeBase.addApi')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormSelect
              label={t('knowledgeBase.agency')}
              name="agency"
              options={agencyOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  agency: option?.value ?? '',
                }))
              }
            />
            <FormSelect
              label={t('knowledgeBase.domain')}
              name="domain"
              options={domainOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  domain: option?.value ?? '',
                }))
              }
            />
            <FormInput
              label={t('knowledgeBase.apiUrl')}
              name="apiUrl"
              type="url"
              placeholder="https://api.example.com/endpoint"
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, apiUrl: e.target.value }))
              }
            />
          </Track>
        </Dialog>
      )}

      {/* Add URL Modal */}
      {addUrlModal && (
        <Dialog
          title={t('knowledgeBase.addUrlTitle')}
          onClose={() => setAddUrlModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setAddUrlModal(false)}
              >
                {t('global.cancel')}
              </Button>
              <Button appearance="primary" onClick={handleAddUrl}>
                {t('knowledgeBase.addUrl')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormSelect
              label={t('knowledgeBase.agency')}
              name="agency"
              options={agencyOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  agency: option?.value ?? '',
                }))
              }
            />
            <FormSelect
              label={t('knowledgeBase.domain')}
              name="domain"
              options={domainOptions}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  domain: option?.value ?? '',
                }))
              }
            />
            <FormInput
              label={t('knowledgeBase.websiteUrl')}
              name="websiteUrl"
              type="url"
              placeholder="https://example.com"
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, websiteUrl: e.target.value }))
              }
            />
          </Track>
        </Dialog>
      )}

      {/* Edit Modal */}
      {editModal && (
        <Dialog
          title={t('knowledgeBase.editTitle')}
          onClose={() => setEditModal(null)}
          footer={
            <Track gap={16} justify="end">
              <Button appearance="secondary" onClick={() => setEditModal(null)}>
                {t('global.cancel')}
              </Button>
              <Button appearance="primary" onClick={handleUpdateItem}>
                {t('global.save')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormSelect
              label={t('knowledgeBase.agency')}
              name="agency"
              options={agencyOptions}
              defaultValue={formData.agency}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  agency: option?.value ?? '',
                }))
              }
            />
            <FormSelect
              label={t('knowledgeBase.domain')}
              name="domain"
              options={domainOptions}
              defaultValue={formData.domain}
              onSelectionChange={(option) =>
                setFormData((prev) => ({
                  ...prev,
                  domain: option?.value ?? '',
                }))
              }
            />
            <FormTextarea
              label={t('knowledgeBase.content')}
              name="content"
              placeholder={t('knowledgeBase.contentPlaceholder')}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, content: e.target.value }))
              }
            />
          </Track>
        </Dialog>
      )}

      {/* Delete Confirmation Modal */}
      {deleteModal && (
        <Dialog
          title={t('knowledgeBase.deleteAgencyTitle')}
          onClose={() => setDeleteModal(null)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setDeleteModal(null)}
              >
                {t('global.cancel')}
              </Button>
              <Button appearance="error" onClick={handleDelete}>
                {t('global.delete')}
              </Button>
            </Track>
          }
        >
          <p>
            {t('knowledgeBase.deleteAgencyConfirmation', {
              agency: deleteModal.agency,
              domain: deleteModal.domain,
            })}
          </p>
        </Dialog>
      )}
    </div>
  );
};

export default Agency;
