"""Account discovery must include future grants and must never switch identity."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure_github import repository_permissions


class RepositoryDiscoveryTests(unittest.TestCase):
    def test_all_pages_include_collaborators_and_future_grants(self):
        first = [{'full_name': f'owner/repo-{i}', 'permissions': {'pull': True, 'push': False}}
                 for i in range(100)]
        second = [{'full_name': 'another-owner/new-project', 'permissions': {'pull': True, 'push': True}}]
        api = Mock(side_effect=[{'login': 'custos-1f916', 'id': 320211121}, first, second])
        result = repository_permissions(api)
        self.assertEqual(len(result), 101)
        self.assertTrue(result['another-owner/new-project']['push'])
        self.assertFalse(result['owner/repo-0']['push'])
        self.assertEqual(api.call_args_list[1].args[0],
                         'user/repos?per_page=100&affiliation=owner,collaborator,organization_member&page=1')
        self.assertTrue(api.call_args_list[2].args[0].endswith('page=2'))

    def test_other_account_is_rejected_before_repo_lookup(self):
        for user in [{'login': 'hal', 'id': 1}, {'login': 'custos-1f916', 'id': 1}]:
            with self.subTest(user=user):
                api = Mock(return_value=user)
                with self.assertRaisesRegex(RuntimeError, 'Expected the Custos'):
                    repository_permissions(api)
                api.assert_called_once_with('user')

    def test_empty_access_does_not_invent_legacy_grants(self):
        api = Mock(side_effect=[{'login': 'custos-1f916', 'id': 320211121}, []])
        self.assertEqual(repository_permissions(api), {})

    def test_later_page_error_does_not_return_partial_inventory(self):
        page = [{'full_name': f'owner/repo-{i}', 'permissions': {'pull': True}} for i in range(100)]
        api = Mock(side_effect=[{'login': 'custos-1f916', 'id': 320211121}, page, RuntimeError('API failure')])
        with self.assertRaisesRegex(RuntimeError, 'API failure'):
            repository_permissions(api)


if __name__ == '__main__':
    unittest.main()
