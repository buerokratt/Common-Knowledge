import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import {
  MdRefresh,
  MdOutlineDeleteOutline,
  MdOutlineViewColumn,
  MdOutlineTableChart,
  MdCheck,
} from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  Icon,
  Track,
  Label,
} from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import './UrlList.scss';

interface UrlItem {
  id: string;
  url: string;
  added: string;
  inModel: boolean;
}

interface AddUrlFormData {
  url: string;
}

const KnowledgeBaseDetail: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { agency, domain } = useParams<{ agency: string; domain: string }>();

  const [addUrlModal, setAddUrlModal] = useState(false);
  const [deleteModal, setDeleteModal] = useState<UrlItem | null>(null);
  const [formData, setFormData] = useState<AddUrlFormData>({ url: '' });

  // Mock data - replace with actual API call
  const { data: urlData, refetch } = useQuery<{
    data: UrlItem[];
    total: number;
  }>({
    queryKey: ['knowledge-base-urls', agency, domain],
    queryFn: async () => ({
      data: [
        {
          id: '1',
          url: 'https://www.riigiteataja.ee/akt/324092024001',
          added: '06.05.2024 10:08',
          inModel: false,
        },
        {
          id: '2',
          url: 'https://www.riigiteataja.ee/akt/324092024001',
          added: '06.05.2024 10:08',
          inModel: true,
        },
        {
          id: '3',
          url: 'https://www.riigiteataja.ee/akt/324092024001',
          added: '06.05.2024 10:08',
          inModel: true,
        },
      ],
      total: 170,
    }),
  });

  const handleAddUrl = async () => {
    try {
      await apiDev.post(`knowledge-base/${agency}/${domain}/urls`, formData);
      setAddUrlModal(false);
      setFormData({ url: '' });
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlAddedSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.urlAddError'),
      });
    }
  };

  const handleRefresh = async (item: UrlItem) => {
    try {
      await apiDev.post(`knowledge-base/urls/${item.id}/refresh`);
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlRefreshed'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.refreshError'),
      });
    }
  };

  const handleDelete = async () => {
    if (!deleteModal) return;

    try {
      await apiDev.delete(`knowledge-base/urls/${deleteModal.id}`);
      setDeleteModal(null);
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlDeleteSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.deleteError'),
      });
    }
  };

  const handleViewRaw = (item: UrlItem) => {
    // Open raw data view
    window.open(`/knowledge-base/urls/${item.id}/raw`, '_blank');
  };

  const handleViewProcessed = (item: UrlItem) => {
    // Open processed data view
    window.open(`/knowledge-base/urls/${item.id}/processed`, '_blank');
  };

  const columns: ColumnDef<UrlItem>[] = [
    {
      accessorKey: 'url',
      header: t('knowledgeBase.url'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div className="knowledge-base-detail__url-cell">
          <a
            href={row.original.url}
            target="_blank"
            rel="noopener noreferrer"
            className="knowledge-base-detail__url-link"
          >
            {row.original.url}
          </a>
        </div>
      ),
    },
    {
      accessorKey: 'added',
      header: t('knowledgeBase.added'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'inModel',
      header: t('knowledgeBase.inModel'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div className="knowledge-base-detail__status-cell">
          {row.original.inModel ? (
            <Label type="success">
              <Icon
                style={{ marginRight: 4, fontSize: 20, alignSelf: 'center' }}
                icon={<MdCheck />}
              />
              {t('global.yes')}
            </Label>
          ) : (
            <Label type="info">{t('global.no')}</Label>
          )}
        </div>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={8} justify="end" align="flex-start">
          <Button
            appearance="text"
            size="s"
            onClick={() => handleRefresh(row.original)}
          >
            <Icon icon={<MdRefresh />} size="small" />
            {t('knowledgeBase.refresh')}
          </Button>
          <Button
            appearance="text"
            size="s"
            onClick={() => handleViewRaw(row.original)}
          >
            <Icon icon={<MdOutlineViewColumn />} size="small" />
            {t('knowledgeBase.raw')}
          </Button>
          <Button
            appearance="text"
            size="s"
            onClick={() => handleViewProcessed(row.original)}
          >
            <Icon icon={<MdOutlineTableChart />} size="small" />
            {t('knowledgeBase.processed')}
          </Button>
          <Button
            appearance="text"
            size="s"
            onClick={() => setDeleteModal(row.original)}
          >
            <Icon icon={<MdOutlineDeleteOutline />} size="small" />
            {t('global.delete')}
          </Button>
        </Track>
      ),
    },
  ];

  return (
    <div className="knowledge-base-detail">
      <Track style={{ marginBottom: 16 }} justify="between" align="center">
        <h1 className="h1">{t('knowledgeBase.title')}</h1>
        <Track gap={12}>
          <Button
            appearance="secondary"
            style={{
              color: '#005AA3',
              borderColor: '#005AA3 !important',
              boxShadow: 'inset 0 0 0 2px #005AA3',
            }}
            onClick={() => setAddUrlModal(true)}
          >
            {t('knowledgeBase.upload')}
          </Button>
          <Button
            appearance="secondary"
            style={{
              color: '#005AA3',
              borderColor: '#005AA3 !important',
              boxShadow: 'inset 0 0 0 2px #005AA3',
            }}
            onClick={() => setAddUrlModal(true)}
          >
            {t('knowledgeBase.addApi')}
          </Button>
          <Button appearance="primary" onClick={() => setAddUrlModal(true)}>
            {t('knowledgeBase.addUrl')}
          </Button>
        </Track>
      </Track>
      <Card
        header={
          <div>
            <span className="knowledge-base-detail__agency">Abc</span>
            <span className="knowledge-base-detail__separator"> / </span>
            <span className="knowledge-base-detail__domain">Domain 1</span>
          </div>
        }
      >
        <DataTable
          data={urlData?.data ?? []}
          columns={columns}
          pagination={{
            pageIndex: 0,
            pageSize: 10,
          }}
          sortable
          filterable
          pagesCount={Math.ceil((urlData?.total ?? 0) / 10)}
        />

        <div className="knowledge-base-detail__footer">
          <span className="knowledge-base-detail__total">
            {urlData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>

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
            <FormInput
              label={t('knowledgeBase.websiteUrl')}
              name="websiteUrl"
              type="url"
              placeholder="https://example.com"
              value={formData.url}
              onChange={(e) => setFormData({ url: e.target.value })}
            />
          </Track>
        </Dialog>
      )}

      {/* Delete Confirmation Modal */}
      {deleteModal && (
        <Dialog
          title={t('knowledgeBase.deleteUrlTitle')}
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
            {t('knowledgeBase.deleteUrlConfirmation', {
              url: deleteModal.url,
            })}
          </p>
        </Dialog>
      )}
    </div>
  );
};

export default KnowledgeBaseDetail;
