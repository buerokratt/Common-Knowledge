import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import {
  MdOutlineDeleteOutline,
  MdAccessTime,
  MdOutlineEdit,
  MdRefresh,
  MdOutlineStopCircle,
} from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  FormSelect,
  Icon,
  Track,
  FileUploader,
} from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import './Agency.scss';
import './AgencyList.scss';
import EditAgency from './AddAgency';
import { Link } from 'react-router-dom';
import type { FileItem } from 'components/FileUploader/FileUploader';

interface KnowledgeBaseItem {
  id: string;
  url: string;
  domain: string;
  lastScraped: string;
  status: string;
}

interface KnowledgeBaseFormData {
  agency: string;
  domain: string;
  content?: string;
  files: FileItem[];
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
    files: [],
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
          url: 'Abc',
          domain: 'Domain 1',
          lastScraped: '31.04.2025',
          status: 'inProgress',
        },
        {
          id: '2',
          url: 'Pvc',
          domain: 'Domain 2',
          lastScraped: '31.04.2025',
          status: 'done',
        },
        {
          id: '3',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'done',
        },
        {
          id: '4',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'inProgress',
        },
        {
          id: '5',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'inProgress',
        },
        {
          id: '6',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'inProgress',
        },
        {
          id: '7',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'inProgress',
        },
        {
          id: '8',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'done',
        },
        {
          id: '9',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'done',
        },
        {
          id: '10',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'done',
        },
        {
          id: '11',
          url: 'Xyz',
          domain: 'Domain 3',
          lastScraped: '31.04.2025',
          status: 'inProgress',
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

  const handleFilesChange = (files: FileItem[]) => {
    setFormData((prev) => ({
      ...prev,
      files,
    }));
  };

  const handleFileDelete = (fileId: string) => {
    setFormData((prev) => ({
      ...prev,
      files: prev.files.filter((file) => file.id !== fileId),
    }));
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
      header: t('knowledgeBase.url'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Link
          to={'/pages'}
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <div className="agencies__agency-cell">{row.original.url}</div>
        </Link>
      ),
    },
    {
      accessorKey: 'domain',
      header: t('knowledgeBase.subsector'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'lastScraped',
      header: t('knowledgeBase.lastScraped'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'status',
      header: t('global.status'),
      cell: ({ row }) => (
        <span
          className={`agencies__status-cell`}
          style={{
            color: row.original.status === 'inProgress' ? '#005AA3' : '#266B42',
            borderColor:
              row.original.status === 'inProgress' ? '#005AA3' : '#266B42',
          }}
        >
          {t(`knowledgeBase.${row.original.status}`)}
        </span>
      ),
      enableColumnFilter: false,
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={32} justify="end">
          {row.original.status === 'inProgress' ? (
            <Button className="agencies__action-btn" appearance="text" size="s">
              <Icon
                icon={<MdOutlineStopCircle fontSize={20} />}
                size="medium"
              />
              {t('global.stop')}
            </Button>
          ) : (
            <Button className="agencies__action-btn" appearance="text" size="s">
              <Icon icon={<MdRefresh fontSize={20} />} size="medium" />
              {t('knowledgeBase.refresh')}
            </Button>
          )}

          <Link
            style={{ display: 'flex', textDecoration: 'none' }}
            to={'/settings'}
          >
            <Button
              disabled={row.original.status === 'inProgress'}
              appearance="text"
              className="agencies__action-btn"
            >
              <Icon icon={<MdAccessTime fontSize={20} />} size="medium" />
              {t('knowledgeBase.scrapeInterval')}
            </Button>
          </Link>
          <Button
            disabled={row.original.status === 'inProgress'}
            appearance="text"
            className="agencies__action-btn"
            onClick={() => setEditModal(true)}
          >
            <Icon icon={<MdOutlineEdit fontSize={20} />} size="medium" />
            {t('global.edit')}
          </Button>
          <Button
            disabled={row.original.status === 'inProgress'}
            appearance="text"
            className="agencies__action-btn"
            onClick={() => setDeleteModal(row.original)}
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
    <div className="agency-container">
      <EditAgency />
      <Card
        header={
          <Track justify="between" align="center">
            <span className="knowledge-base-detail__agency">
              {t('knowledgeBase.sources')}
            </span>
            <Track gap={16}>
              <Button
                appearance="secondary"
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3 !important',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                }}
                onClick={() => setUploadModal(true)}
              >
                {t('knowledgeBase.uploadFile')}
              </Button>
              <Button appearance="primary" onClick={() => setAddUrlModal(true)}>
                {t('knowledgeBase.addUrl')}
              </Button>
            </Track>
          </Track>
        }
      >
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
          title={t('knowledgeBase.uploadFile')}
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
            <FormInput
              className="url-input"
              label={t('knowledgeBase.subsector')}
              name="subsector"
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, websiteUrl: e.target.value }))
              }
            />
            <FileUploader
              files={formData.files}
              onFilesChange={handleFilesChange}
              onFileDelete={handleFileDelete}
              maxFileSize={30 * 1024 * 1024} // 30MB
              acceptedTypes=".pdf,.doc,.docx,.txt,.html,.htm"
              multiple={true}
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
          title={t('knowledgeBase.addUrl')}
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
                {t('global.add')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormInput
              className="url-input"
              label={t('knowledgeBase.subsector')}
              name="subsector"
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, websiteUrl: e.target.value }))
              }
            />
            <FormInput
              className="url-input"
              label={t('knowledgeBase.url')}
              name="websiteUrl"
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
          title={t('knowledgeBase.editSource')}
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
            <FormInput
              className="url-input"
              label={t('knowledgeBase.subsector')}
              name="subsector"
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, websiteUrl: e.target.value }))
              }
            />
          </Track>
        </Dialog>
      )}

      {/* Delete Confirmation Modal */}
      {deleteModal && (
        <Dialog
          title={t('knowledgeBase.deleteSource')}
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
          {t('knowledgeBase.deleteSourceConfirmation')}
        </Dialog>
      )}
    </div>
  );
};

export default Agency;
