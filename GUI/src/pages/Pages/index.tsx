import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import {
  MdRefresh,
  MdOutlineViewColumn,
  MdOutlineTableChart,
  MdGridView,
} from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  Icon,
  Track,
  SwitchBox,
  Editor,
} from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import 'pages/Agency/AgencyList.scss';

interface UrlItem {
  id: string;
  url: string;
  scraped: string;
  pageTitle: string;
  status: string;
}

interface FormData {
  url: string;
  search: string;
}

const KnowledgeBaseDetail: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const { agency, domain } = useParams<{ agency: string; domain: string }>();

  const [addUrlModal, setAddUrlModal] = useState(false);
  const [editorState, setEditorState] = useState<
    'raw' | 'cleaned' | 'edited' | null
  >(null);
  const [deleteModal, setDeleteModal] = useState<UrlItem | null>(null);
  const [formData, setFormData] = useState<FormData>({
    url: '',
    search: '',
  });

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
          scraped: '06.05.2024 10:08',
          pageTitle: 'Eraisik',
          status: 'done',
        },
        {
          id: '2',
          url: 'https://www.riigiteataja.ee/akt/324092024001',
          scraped: '06.05.2024 10:08',
          pageTitle: 'Ettevõte',
          status: 'cleaning',
        },
        {
          id: '3',
          url: 'https://www.riigiteataja.ee/akt/324092024001',
          scraped: '06.05.2024 10:08',
          pageTitle: 'Kontakt',
          status: 'notFound',
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
        <a
          href={row.original.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <div className="agencies__agency-cell">{row.original.url}</div>
        </a>
      ),
    },
    {
      accessorKey: 'pageTitle',
      header: t('knowledgeBase.pageTitle'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div style={{ minWidth: 100 }}>{row.original.pageTitle}</div>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track
          justify="end"
          align="flex-start"
          gap={32}
          style={{ width: 'max-content' }}
        >
          <Button
            disabled={row.original.status === 'cleaning'}
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleRefresh(row.original)}
          >
            <Icon icon={<MdRefresh fontSize={20} />} size="medium" />
            {t('knowledgeBase.refresh')}
          </Button>
          <Button
            disabled={row.original.status === 'cleaning'}
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => setEditorState('raw')}
          >
            <Icon icon={<MdOutlineViewColumn fontSize={20} />} size="medium" />
            {t('knowledgeBase.raw')}
          </Button>
          <Button
            disabled={row.original.status === 'cleaning'}
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => setEditorState('cleaned')}
          >
            <Icon icon={<MdOutlineTableChart fontSize={20} />} size="medium" />
            {t('knowledgeBase.cleaned')}
          </Button>
          <Button
            appearance="text"
            disabled={row.original.status === 'cleaning'}
            className="agencies__action-btn"
            size="s"
            onClick={() => setEditorState('edited')}
          >
            <Icon icon={<MdGridView fontSize={20} />} size="medium" />
            {t('knowledgeBase.edited')}
          </Button>
        </Track>
      ),
    },
    {
      id: 'excluded',
      header: (
        <div
          style={{ display: 'flex', justifyContent: 'center', width: '100%' }}
        >
          {t('knowledgeBase.excluded')}
        </div>
      ),
      cell: ({ row }) => <SwitchBox label="" />,
    },
    {
      accessorKey: 'status',
      header: t('global.status'),
      cell: ({ row }) => {
        const color =
          row.original.status === 'done'
            ? '#266B42'
            : row.original.status === 'cleaning'
            ? '#94690D'
            : '#AC3232';
        return (
          <span
            className={`agencies__status-cell`}
            style={{
              color: color,
              borderColor: color,
            }}
          >
            {t(`knowledgeBase.${row.original.status}`)}
          </span>
        );
      },
      enableColumnFilter: false,
    },
    {
      accessorKey: 'scraped',
      header: t('knowledgeBase.scraped'),
      enableColumnFilter: false,
    },
  ];

  return (
    <>
      {editorState && (
        <Dialog size="fullscreen" onClose={() => setEditorState(null)}>
          <Editor
            changeEditorState={setEditorState}
            editorState={editorState}
            onCancel={() => setEditorState(null)}
            onSave={() => setEditorState(null)}
          />
        </Dialog>
      )}
      <div className="agencies">
        <Track
          style={{ marginBottom: 16, width: '100%' }}
          justify="between"
          align="center"
        >
          <div>
            <span className="agencies__agency">EMTA / www.emta.ee</span>
          </div>
        </Track>

        <Card
          header={
            <Track gap={16} justify="end" align="center">
              <FormInput
                className="agencies__search"
                label={t('knowledgeBase.searchWithinListedSources')}
                name="search"
                value={formData.search}
                onChange={(e) => setFormData({ search: e.target.value })}
              />
              <Button appearance="primary">{t('global.search')}</Button>
            </Track>
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

          <div className="agencies__footer">
            <span className="agencies__total">
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
    </>
  );
};

export default KnowledgeBaseDetail;
